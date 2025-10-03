"""
监控模块 - 用于收集和管理请求统计数据
"""

import json
import time
import threading
from datetime import datetime, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 配置
class MonitorConfig:
    """监控配置"""
    LOG_DIR = Path("logs")
    REQUEST_LOG_FILE = "requests.jsonl"
    ERROR_LOG_FILE = "errors.jsonl"
    STATS_FILE = "stats.json"  # 新增：统计数据文件
    MAX_LOG_SIZE = 400 * 1024 * 1024  # 10MB
    MAX_LOG_FILES = 10
    MAX_RECENT_REQUESTS = 10000
    MAX_RECENT_ERRORS = 50
    STATS_UPDATE_INTERVAL = 5  # 秒

# 确保日志目录存在
MonitorConfig.LOG_DIR.mkdir(exist_ok=True)

@dataclass
class RequestInfo:
    """请求信息"""
    request_id: str
    timestamp: float
    model: str
    status: str  # 'active', 'success', 'failed'
    duration: Optional[float] = None
    error: Optional[str] = None
    messages_count: int = 0
    session_id: Optional[str] = None
    mode: Optional[str] = None
    # 新增详细信息字段
    request_messages: Optional[List[dict]] = None
    request_params: Optional[dict] = None
    response_content: Optional[str] = None
    reasoning_content: Optional[str] = None  # 新增：思维链内容
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass
class Stats:
    """统计数据"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    active_requests: int = 0
    avg_duration: float = 0.0
    total_messages: int = 0
    uptime: float = 0.0

class LogManager:
    """日志管理器"""
    
    def __init__(self):
        self.request_log_path = MonitorConfig.LOG_DIR / MonitorConfig.REQUEST_LOG_FILE
        self.error_log_path = MonitorConfig.LOG_DIR / MonitorConfig.ERROR_LOG_FILE
        self._lock = threading.Lock()
        
    def write_request_log(self, log_entry: dict):
        """写入请求日志"""
        with self._lock:
            try:
                with open(self.request_log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except Exception as e:
                logger.error(f"写入请求日志失败: {e}")
    
    def write_error_log(self, log_entry: dict):
        """写入错误日志"""
        with self._lock:
            try:
                with open(self.error_log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except Exception as e:
                logger.error(f"写入错误日志失败: {e}")
    
    def read_recent_logs(self, log_type: str = "requests", limit: int = 50) -> List[dict]:
        """读取最近的日志（仅返回完成的请求）"""
        log_path = self.request_log_path if log_type == "requests" else self.error_log_path
        logs = []
        
        if not log_path.exists():
            return logs
            
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # 从后往前读取，收集最近的 request_end 类型日志
                for line in reversed(lines):
                    if len(logs) >= limit:
                        break
                    try:
                        log_entry = json.loads(line.strip())
                        # 只返回 request_end 类型的日志（包含完整信息）
                        if log_type == "requests" and log_entry.get('type') == 'request_end':
                            logs.append(log_entry)
                        elif log_type == "errors":
                            # 错误日志不需要过滤
                            logs.append(log_entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"读取日志失败: {e}")
            
        return logs  # 已经是倒序的（最新的在前）

class MonitoringService:
    """监控服务"""
    
    def __init__(self):
        self.startup_time = time.time()
        self.log_manager = LogManager()
        self.active_requests: Dict[str, RequestInfo] = {}
        self.recent_requests = deque(maxlen=MonitorConfig.MAX_RECENT_REQUESTS)
        self.recent_errors = deque(maxlen=MonitorConfig.MAX_RECENT_ERRORS)
        self.model_stats = defaultdict(lambda: {
            'total': 0, 'success': 0, 'failed': 0,
            'total_duration': 0, 'count_with_duration': 0
        })
        self._lock = threading.Lock()
        
        # 新增：存储完整的请求详情（用于详情查看）
        # 使用OrderedDict实现更好的内存管理
        from collections import OrderedDict
        self.request_details_cache = OrderedDict()  # 使用OrderedDict管理缓存
        self.MAX_DETAILS_CACHE = 10000  # 保持原有的缓存大小
        self.cache_size_limit_mb = 500  # 增加缓存大小限制为500MB，确保数据完整性
        
        # WebSocket客户端管理
        self.monitor_clients = set()
        
        # 加载持久化的统计数据
        self._load_persisted_stats()
        
        logger.info("监控服务已初始化")
    
    def request_start(self, request_id: str, model: str, messages_count: int = 0,
                     session_id: str = None, mode: str = None,
                     messages: List[dict] = None, params: dict = None):
        """记录请求开始（增加详细信息）"""
        with self._lock:
            # 计算输入token的估算值
            estimated_input_tokens = 0
            if messages:
                for msg in messages:
                    if isinstance(msg, dict) and 'content' in msg:
                        content = msg.get('content', '')
                        if isinstance(content, str):
                            estimated_input_tokens += len(content) // 4
                        elif isinstance(content, list):
                            # 处理多模态消息
                            for part in content:
                                if isinstance(part, dict) and part.get('type') == 'text':
                                    estimated_input_tokens += len(part.get('text', '')) // 4
            
            request_info = RequestInfo(
                request_id=request_id,
                timestamp=time.time(),
                model=model,
                status='active',
                messages_count=messages_count,
                session_id=session_id,
                mode=mode,
                request_messages=messages,
                request_params=params,
                input_tokens=estimated_input_tokens  # 设置估算的输入token
            )
            self.active_requests[request_id] = request_info
            
            # 同时存储到详情缓存
            self._store_request_details(request_id, request_info)
            
            # 写入日志
            log_entry = {
                'type': 'request_start',
                'timestamp': request_info.timestamp,
                'request_id': request_id,
                'model': model,
                'messages_count': messages_count,
                'session_id': session_id,
                'mode': mode
            }
            self.log_manager.write_request_log(log_entry)
            
            logger.info(f"请求开始 [ID: {request_id[:8]}] 模型: {model}")
    
    def request_end(self, request_id: str, success: bool, error: str = None,
                    response_content: str = None, reasoning_content: str = None,
                    input_tokens: int = 0, output_tokens: int = 0):
        """记录请求结束（增加响应内容和思维链）"""
        with self._lock:
            if request_id not in self.active_requests:
                logger.warning(f"未找到请求 {request_id}")
                return
                
            request_info = self.active_requests[request_id]
            request_info.status = 'success' if success else 'failed'
            request_info.duration = time.time() - request_info.timestamp
            request_info.error = error
            request_info.response_content = response_content
            request_info.reasoning_content = reasoning_content
            request_info.input_tokens = input_tokens
            request_info.output_tokens = output_tokens
            
            # 更新详情缓存
            self._store_request_details(request_id, request_info)
            
            # 更新模型统计
            model = request_info.model
            self.model_stats[model]['total'] += 1
            if success:
                self.model_stats[model]['success'] += 1
            else:
                self.model_stats[model]['failed'] += 1
                
            if request_info.duration:
                self.model_stats[model]['total_duration'] += request_info.duration
                self.model_stats[model]['count_with_duration'] += 1
            
            # 持久化统计数据
            self._persist_stats()
            
            # 添加到最近请求列表
            self.recent_requests.append(asdict(request_info))
            
            # 如果失败，添加到错误列表
            if not success:
                error_info = {
                    'timestamp': time.time(),
                    'request_id': request_id,
                    'model': model,
                    'error': error or 'Unknown error'
                }
                self.recent_errors.append(error_info)
                self.log_manager.write_error_log(error_info)
            
            # 写入请求日志（包含完整详情）
            log_entry = {
                'type': 'request_end',
                'timestamp': time.time(),
                'request_id': request_id,
                'model': model,
                'status': request_info.status,
                'duration': request_info.duration,
                'error': error,
                'mode': request_info.mode,
                'session_id': request_info.session_id,
                'messages_count': request_info.messages_count,
                'input_tokens': request_info.input_tokens,
                'output_tokens': request_info.output_tokens,
                # 包含详细信息
                'request_messages': request_info.request_messages,
                'request_params': request_info.request_params,
                'response_content': request_info.response_content,
                'reasoning_content': request_info.reasoning_content
            }
            self.log_manager.write_request_log(log_entry)
            
            # 从活动请求中移除
            del self.active_requests[request_id]
            
            logger.info(f"请求结束 [ID: {request_id[:8]}] 状态: {request_info.status} 耗时: {request_info.duration:.2f}s")
    
    def get_stats(self) -> Stats:
        """获取统计数据"""
        with self._lock:
            stats = Stats()
            stats.uptime = time.time() - self.startup_time
            stats.active_requests = len(self.active_requests)
            
            # 获取所有时间的统计（从持久化数据）
            # 优先使用持久化的总数，这样即使重启服务器也能保持准确
            total_all_time = sum(s['total'] for s in self.model_stats.values())
            success_all_time = sum(s['success'] for s in self.model_stats.values())
            failed_all_time = sum(s['failed'] for s in self.model_stats.values())
            
            # 使用所有时间的总数
            stats.total_requests = total_all_time
            stats.successful_requests = success_all_time
            stats.failed_requests = failed_all_time
            
            # 计算总消息数（从最近的请求中累加）
            stats.total_messages = sum(req.get('messages_count', 0) for req in self.recent_requests)
            
            # 计算平均响应时间（使用最近100个请求）
            recent_durations = []
            for req in list(self.recent_requests)[-100:]:  # 最近100个请求
                if req.get('duration'):
                    recent_durations.append(req['duration'])
            
            if recent_durations:
                stats.avg_duration = sum(recent_durations) / len(recent_durations)
                
            return stats
    
    def get_model_stats(self) -> List[dict]:
        """获取模型统计"""
        with self._lock:
            model_stats_list = []
            for model, stats in self.model_stats.items():
                avg_duration = 0
                if stats['count_with_duration'] > 0:
                    avg_duration = stats['total_duration'] / stats['count_with_duration']
                    
                success_rate = 0
                if stats['total'] > 0:
                    success_rate = (stats['success'] / stats['total']) * 100
                    
                model_stats_list.append({
                    'model': model,
                    'total_requests': stats['total'],
                    'successful_requests': stats['success'],
                    'failed_requests': stats['failed'],
                    'avg_duration': avg_duration,
                    'success_rate': success_rate
                })
            
            # 按总请求数排序
            model_stats_list.sort(key=lambda x: x['total_requests'], reverse=True)
            return model_stats_list
    
    def get_active_requests(self) -> List[dict]:
        """获取活动请求列表"""
        with self._lock:
            return [asdict(req) for req in self.active_requests.values()]
    
    def get_recent_requests(self, limit: int = 50) -> List[dict]:
        """获取最近的请求"""
        with self._lock:
            requests = list(self.recent_requests)
            return requests[-limit:][::-1]  # 最新的在前
    
    def get_recent_errors(self, limit: int = 30) -> List[dict]:
        """获取最近的错误"""
        with self._lock:
            errors = list(self.recent_errors)
            return errors[-limit:][::-1]  # 最新的在前
    
    def get_summary(self) -> dict:
        """获取监控摘要"""
        stats = self.get_stats()
        model_stats = self.get_model_stats()
        
        return {
            'stats': asdict(stats),
            'model_stats': model_stats,
            'active_requests_list': self.get_active_requests(),
            'recent_errors_count': len(self.recent_errors)
        }
    
    async def broadcast_to_monitors(self, data: dict):
        """向所有监控客户端广播数据"""
        if not self.monitor_clients:
            return
            
        disconnected = []
        for client in self.monitor_clients:
            try:
                await client.send_json(data)
            except:
                disconnected.append(client)
        
        # 清理断开的连接
        for client in disconnected:
            self.monitor_clients.discard(client)
    
    def add_monitor_client(self, websocket):
        """添加监控客户端"""
        self.monitor_clients.add(websocket)
        logger.debug(f"监控客户端已连接，当前客户端数: {len(self.monitor_clients)}")
    
    def remove_monitor_client(self, websocket):
        """移除监控客户端"""
        self.monitor_clients.discard(websocket)
        logger.debug(f"监控客户端已断开，当前客户端数: {len(self.monitor_clients)}")
    
    def _store_request_details(self, request_id: str, request_info: RequestInfo):
        """存储请求详情到缓存（保持数据完整性）"""
        import sys
        
        # 创建要存储的数据 - 保持完整性，不截断
        request_data = asdict(request_info)
        
        # 检查缓存大小（粗略估算）
        cache_size_bytes = sys.getsizeof(self.request_details_cache)
        cache_size_mb = cache_size_bytes / (1024 * 1024)
        
        # 如果缓存过大（超过500MB），删除最老的10%项目
        if cache_size_mb > self.cache_size_limit_mb and len(self.request_details_cache) > 0:
            # 删除最老的10%项目
            items_to_remove = max(1, len(self.request_details_cache) // 10)
            for _ in range(items_to_remove):
                self.request_details_cache.popitem(last=False)
            cache_size_bytes = sys.getsizeof(self.request_details_cache)
            cache_size_mb = cache_size_bytes / (1024 * 1024)
            logger.info(f"[CACHE] 缓存超过限制，已清理 {items_to_remove} 个旧项，当前大小: ~{cache_size_mb:.2f}MB")
        
        # 限制缓存项数
        if len(self.request_details_cache) >= self.MAX_DETAILS_CACHE:
            # 删除最老的缓存项（FIFO）
            self.request_details_cache.popitem(last=False)
        
        # 存储新项 - 保持数据完整
        self.request_details_cache[request_id] = request_data
        
        # 定期记录缓存状态（每500个请求）
        if len(self.request_details_cache) % 500 == 0:
            logger.debug(f"[CACHE] 详情缓存状态 - 项数: {len(self.request_details_cache)}, 大小: ~{cache_size_mb:.2f}MB")
    
    def get_request_details(self, request_id: str) -> Optional[dict]:
        """获取请求详情"""
        with self._lock:
            # 先从缓存中查找
            if request_id in self.request_details_cache:
                return self.request_details_cache[request_id]
            
            # 从活跃请求中查找
            if request_id in self.active_requests:
                return asdict(self.active_requests[request_id])
            
            # 从最近请求中查找
            for req in self.recent_requests:
                if req.get('request_id') == request_id:
                    return req
            
            # 如果内存中都没有，从日志文件中查找
            return self._find_request_in_logs(request_id)
    
    def _find_request_in_logs(self, request_id: str) -> Optional[dict]:
        """从日志文件中查找请求详情"""
        try:
            if not self.log_manager.request_log_path.exists():
                return None
            
            with open(self.log_manager.request_log_path, 'r', encoding='utf-8') as f:
                # 从后往前读取，提高查找效率
                lines = f.readlines()
                for line in reversed(lines):
                    try:
                        log_entry = json.loads(line.strip())
                        if (log_entry.get('request_id') == request_id and
                            log_entry.get('type') == 'request_end'):
                            # 找到了完整的请求记录
                            return log_entry
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"从日志文件查找请求详情失败: {e}")
        
        return None
    
    def _persist_stats(self):
        """持久化统计数据到文件"""
        try:
            stats_path = MonitorConfig.LOG_DIR / MonitorConfig.STATS_FILE
            
            # 准备要保存的数据
            stats_data = {
                'last_update': time.time(),
                'startup_time': self.startup_time,
                'model_stats': dict(self.model_stats),
                # 保存总体统计
                'total_requests_all_time': sum(s['total'] for s in self.model_stats.values()),
                'total_success_all_time': sum(s['success'] for s in self.model_stats.values()),
                'total_failed_all_time': sum(s['failed'] for s in self.model_stats.values())
            }
            
            # 写入文件
            with open(stats_path, 'w', encoding='utf-8') as f:
                json.dump(stats_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"持久化统计数据失败: {e}")
    
    def _load_persisted_stats(self):
        """从文件加载持久化的统计数据"""
        try:
            stats_path = MonitorConfig.LOG_DIR / MonitorConfig.STATS_FILE
            
            if not stats_path.exists():
                logger.info("未找到持久化统计数据，将从零开始")
                return
            
            with open(stats_path, 'r', encoding='utf-8') as f:
                stats_data = json.load(f)
            
            # 恢复模型统计
            if 'model_stats' in stats_data:
                self.model_stats = defaultdict(
                    lambda: {'total': 0, 'success': 0, 'failed': 0,
                            'total_duration': 0, 'count_with_duration': 0},
                    stats_data['model_stats']
                )
            
            # 恢复最近的请求和错误
            if 'recent_requests' in stats_data:
                for req in stats_data['recent_requests']:
                    self.recent_requests.append(req)
            
            if 'recent_errors' in stats_data:
                for err in stats_data['recent_errors']:
                    self.recent_errors.append(err)
            
            # 如果是同一次运行会话，保持原有的启动时间
            # 否则重置启动时间
            if 'startup_time' in stats_data:
                time_since_last_update = time.time() - stats_data.get('last_update', 0)
                # 如果距离上次更新超过1小时，认为是新的会话
                if time_since_last_update > 3600:
                    self.startup_time = time.time()
                else:
                    self.startup_time = stats_data['startup_time']
            
            logger.info(f"已加载持久化统计数据：{len(self.model_stats)} 个模型统计")
            
        except Exception as e:
            logger.error(f"加载持久化统计数据失败: {e}")
    
    def get_all_time_stats(self) -> dict:
        """获取所有时间的统计数据（从日志文件计算）"""
        try:
            if not self.log_manager.request_log_path.exists():
                return {
                    'total_requests': 0,
                    'total_success': 0,
                    'total_failed': 0,
                    'models': {}
                }
            
            model_counts = defaultdict(lambda: {'total': 0, 'success': 0, 'failed': 0})
            total_requests = 0
            total_success = 0
            total_failed = 0
            
            with open(self.log_manager.request_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        log_entry = json.loads(line.strip())
                        if log_entry.get('type') == 'request_end':
                            model = log_entry.get('model', 'unknown')
                            status = log_entry.get('status', 'failed')
                            
                            total_requests += 1
                            model_counts[model]['total'] += 1
                            
                            if status == 'success':
                                total_success += 1
                                model_counts[model]['success'] += 1
                            else:
                                total_failed += 1
                                model_counts[model]['failed'] += 1
                                
                    except json.JSONDecodeError:
                        continue
            
            return {
                'total_requests': total_requests,
                'total_success': total_success,
                'total_failed': total_failed,
                'models': dict(model_counts)
            }
            
        except Exception as e:
            logger.error(f"计算所有时间统计失败: {e}")
            return {
                'total_requests': 0,
                'total_success': 0,
                'total_failed': 0,
                'models': {}
            }

# 创建全局监控服务实例
monitoring_service = MonitoringService()