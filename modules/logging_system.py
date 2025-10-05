"""
新的日志系统 - 完全重写，提供更可靠的日志记录
使用结构化日志、异步写入、自动轮转和错误恢复机制
"""

import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from collections import deque
import threading
import time
from enum import Enum

# 配置标准Python日志
logger = logging.getLogger(__name__)


class LogLevel(Enum):
    """日志级别"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogType(Enum):
    """日志类型"""
    REQUEST = "request"
    ERROR = "error"
    SYSTEM = "system"
    PERFORMANCE = "performance"


@dataclass
class LogEntry:
    """日志条目"""
    timestamp: float
    level: str
    log_type: str
    message: str
    data: Dict[str, Any]
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'timestamp': self.timestamp,
            'datetime': datetime.fromtimestamp(self.timestamp).isoformat(),
            'level': self.level,
            'type': self.log_type,
            'message': self.message,
            'data': self.data
        }
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class LogBuffer:
    """日志缓冲区 - 用于批量写入"""
    
    def __init__(self, max_size: int = 100, flush_interval: float = 5.0):
        self.buffer = deque(maxlen=max_size)
        self.max_size = max_size
        self.flush_interval = flush_interval
        self.last_flush = time.time()
        self._lock = threading.Lock()
    
    def add(self, entry: LogEntry) -> bool:
        """添加日志条目"""
        with self._lock:
            self.buffer.append(entry)
            # 如果缓冲区满了或距离上次刷新时间过长，返回True表示需要刷新
            return (len(self.buffer) >= self.max_size or 
                    time.time() - self.last_flush >= self.flush_interval)
    
    def get_all(self) -> List[LogEntry]:
        """获取所有缓冲的日志并清空"""
        with self._lock:
            entries = list(self.buffer)
            self.buffer.clear()
            self.last_flush = time.time()
            return entries
    
    def size(self) -> int:
        """获取缓冲区大小"""
        with self._lock:
            return len(self.buffer)


class LogWriter:
    """日志写入器 - 负责实际的文件写入"""
    
    def __init__(self, log_dir: Path, log_name: str, max_size_mb: int = 50):
        self.log_dir = log_dir
        self.log_name = log_name
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.current_file = self.log_dir / f"{log_name}.jsonl"
        self._lock = threading.Lock()
        self._ensure_log_dir()
    
    def _ensure_log_dir(self):
        """确保日志目录存在"""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建日志目录失败: {e}")
    
    def _rotate_if_needed(self):
        """如果文件过大则轮转"""
        try:
            if self.current_file.exists() and self.current_file.stat().st_size > self.max_size_bytes:
                # 生成轮转文件名
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                rotated_file = self.log_dir / f"{self.log_name}_{timestamp}.jsonl"
                
                # 重命名当前文件
                self.current_file.rename(rotated_file)
                logger.info(f"日志文件已轮转: {rotated_file.name}")
                
                # 清理旧文件（保留最近10个）
                self._cleanup_old_logs()
        except Exception as e:
            logger.error(f"日志轮转失败: {e}")
    
    def _cleanup_old_logs(self):
        """清理旧的日志文件"""
        try:
            # 获取所有轮转的日志文件
            pattern = f"{self.log_name}_*.jsonl"
            log_files = sorted(self.log_dir.glob(pattern), key=lambda x: x.stat().st_mtime)
            
            # 保留最近的10个文件
            if len(log_files) > 10:
                for old_file in log_files[:-10]:
                    old_file.unlink()
                    logger.info(f"已删除旧日志文件: {old_file.name}")
        except Exception as e:
            logger.error(f"清理旧日志失败: {e}")
    
    def write_batch(self, entries: List[LogEntry]) -> bool:
        """批量写入日志"""
        if not entries:
            return True
        
        with self._lock:
            try:
                # 检查是否需要轮转
                self._rotate_if_needed()
                
                # 批量写入
                with open(self.current_file, 'a', encoding='utf-8') as f:
                    for entry in entries:
                        f.write(entry.to_json() + '\n')
                
                return True
            except Exception as e:
                logger.error(f"批量写入日志失败: {e}")
                # 尝试逐条写入（降级处理）
                return self._write_one_by_one(entries)
    
    def _write_one_by_one(self, entries: List[LogEntry]) -> bool:
        """逐条写入日志（降级处理）"""
        success_count = 0
        for entry in entries:
            try:
                with open(self.current_file, 'a', encoding='utf-8') as f:
                    f.write(entry.to_json() + '\n')
                success_count += 1
            except Exception as e:
                logger.error(f"写入单条日志失败: {e}")
        
        return success_count > 0
    
    def read_recent(self, limit: int = 100) -> List[dict]:
        """读取最近的日志"""
        logs = []
        
        if not self.current_file.exists():
            return logs
        
        try:
            with open(self.current_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # 从后往前读取
                for line in reversed(lines[-limit:]):
                    try:
                        log_data = json.loads(line.strip())
                        logs.append(log_data)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"读取日志失败: {e}")
        
        return logs


class AsyncLogSystem:
    """异步日志系统 - 主类"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 为不同类型的日志创建独立的缓冲区和写入器
        self.buffers = {
            LogType.REQUEST: LogBuffer(max_size=50, flush_interval=3.0),
            LogType.ERROR: LogBuffer(max_size=20, flush_interval=1.0),
            LogType.SYSTEM: LogBuffer(max_size=30, flush_interval=5.0),
            LogType.PERFORMANCE: LogBuffer(max_size=40, flush_interval=10.0)
        }
        
        self.writers = {
            LogType.REQUEST: LogWriter(self.log_dir, "requests", max_size_mb=50),
            LogType.ERROR: LogWriter(self.log_dir, "errors", max_size_mb=10),
            LogType.SYSTEM: LogWriter(self.log_dir, "system", max_size_mb=20),
            LogType.PERFORMANCE: LogWriter(self.log_dir, "performance", max_size_mb=30)
        }
        
        # 内存中的最近日志（用于快速查询）
        self.recent_logs = {
            LogType.REQUEST: deque(maxlen=1000),
            LogType.ERROR: deque(maxlen=200),
            LogType.SYSTEM: deque(maxlen=500),
            LogType.PERFORMANCE: deque(maxlen=500)
        }
        
        # 后台刷新任务
        self._running = False
        self._flush_task = None
        
        logger.info("异步日志系统已初始化")
    
    def start(self):
        """启动后台刷新任务"""
        if self._running:
            return
        
        self._running = True
        self._flush_task = asyncio.create_task(self._background_flush())
        logger.info("日志系统后台任务已启动")
    
    async def stop(self):
        """停止后台任务并刷新所有缓冲"""
        self._running = False
        if self._flush_task:
            await self._flush_task
        
        # 最后刷新所有缓冲区
        await self._flush_all()
        logger.info("日志系统已停止")
    
    async def _background_flush(self):
        """后台定期刷新缓冲区"""
        while self._running:
            try:
                await asyncio.sleep(1.0)  # 每秒检查一次
                await self._flush_all()
            except Exception as e:
                logger.error(f"后台刷新任务出错: {e}")
    
    async def _flush_all(self):
        """刷新所有缓冲区"""
        for log_type in LogType:
            await self._flush_buffer(log_type)
    
    async def _flush_buffer(self, log_type: LogType):
        """刷新指定类型的缓冲区"""
        buffer = self.buffers[log_type]
        writer = self.writers[log_type]
        
        entries = buffer.get_all()
        if entries:
            # 在线程池中执行写入操作（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, writer.write_batch, entries)
    
    def log(self, level: LogLevel, log_type: LogType, message: str, **data):
        """记录日志"""
        entry = LogEntry(
            timestamp=time.time(),
            level=level.value,
            log_type=log_type.value,
            message=message,
            data=data
        )
        
        # 添加到缓冲区
        buffer = self.buffers[log_type]
        should_flush = buffer.add(entry)
        
        # 添加到内存缓存
        self.recent_logs[log_type].append(entry.to_dict())
        
        # 如果需要立即刷新（缓冲区满或时间到）
        if should_flush:
            asyncio.create_task(self._flush_buffer(log_type))
        
        # 同时输出到标准日志
        log_func = getattr(logger, level.value.lower(), logger.info)
        log_func(f"[{log_type.value.upper()}] {message}")
    
    def log_request_start(self, request_id: str, model: str, **kwargs):
        """记录请求开始"""
        self.log(
            LogLevel.INFO,
            LogType.REQUEST,
            f"请求开始 [{request_id[:8]}]",
            request_id=request_id,
            model=model,
            event="request_start",
            **kwargs
        )
    
    def log_request_end(self, request_id: str, success: bool, duration: float, **kwargs):
        """记录请求结束"""
        level = LogLevel.INFO if success else LogLevel.ERROR
        status = "成功" if success else "失败"
        
        self.log(
            level,
            LogType.REQUEST,
            f"请求{status} [{request_id[:8]}] 耗时: {duration:.2f}s",
            request_id=request_id,
            success=success,
            duration=duration,
            event="request_end",
            **kwargs
        )
    
    def log_error(self, error_type: str, message: str, **kwargs):
        """记录错误"""
        self.log(
            LogLevel.ERROR,
            LogType.ERROR,
            message,
            error_type=error_type,
            **kwargs
        )
    
    def log_system(self, message: str, **kwargs):
        """记录系统事件"""
        self.log(
            LogLevel.INFO,
            LogType.SYSTEM,
            message,
            **kwargs
        )
    
    def log_performance(self, metric: str, value: float, **kwargs):
        """记录性能指标"""
        self.log(
            LogLevel.INFO,
            LogType.PERFORMANCE,
            f"{metric}: {value}",
            metric=metric,
            value=value,
            **kwargs
        )
    
    def get_recent_logs(self, log_type: LogType, limit: int = 100) -> List[dict]:
        """获取最近的日志（从内存）"""
        logs = list(self.recent_logs[log_type])
        return logs[-limit:][::-1]  # 最新的在前
    
    def get_logs_from_file(self, log_type: LogType, limit: int = 100) -> List[dict]:
        """从文件读取日志"""
        writer = self.writers[log_type]
        return writer.read_recent(limit)
    
    def get_buffer_status(self) -> dict:
        """获取缓冲区状态"""
        return {
            log_type.value: {
                'buffered': self.buffers[log_type].size(),
                'max_size': self.buffers[log_type].max_size,
                'in_memory': len(self.recent_logs[log_type])
            }
            for log_type in LogType
        }


# 创建全局日志系统实例
log_system = AsyncLogSystem()
