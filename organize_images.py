#!/usr/bin/env python3
"""
图片整理脚本
将 downloaded_images 目录中的图片按日期分类到子文件夹
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
import re
import json

class ImageOrganizer:
    def __init__(self, base_dir="downloaded_images"):
        self.base_dir = Path(base_dir)
        self.stats = {
            "total_files": 0,
            "moved_files": 0,
            "skipped_files": 0,
            "special_files": 0,
            "errors": 0,
            "folders_created": 0
        }
        self.file_moves = []
        self.errors = []
        self.special_files = []
        
    def organize(self):
        """主整理函数"""
        print("=" * 60)
        print("🗂️  开始整理图片文件...")
        print("=" * 60)
        
        if not self.base_dir.exists():
            print(f"❌ 目录 {self.base_dir} 不存在！")
            return
        
        # 获取所有文件
        files = [f for f in self.base_dir.iterdir() if f.is_file()]
        self.stats["total_files"] = len(files)
        
        print(f"📊 找到 {len(files)} 个文件待整理\n")
        
        # 处理每个文件
        for file_path in files:
            self.process_file(file_path)
        
        # 显示整理结果
        self.show_results()
    
    def process_file(self, file_path):
        """处理单个文件"""
        filename = file_path.name
        
        # 跳过非图片文件
        if not self.is_image_file(filename):
            print(f"⏭️  跳过非图片文件: {filename}")
            self.stats["skipped_files"] += 1
            return
        
        # 尝试从文件名提取日期
        date_folder = self.extract_date_from_filename(filename)
        
        if date_folder:
            # 创建日期文件夹并移动文件
            self.move_to_date_folder(file_path, date_folder)
        else:
            # 特殊文件处理
            self.handle_special_file(file_path)
    
    def is_image_file(self, filename):
        """检查是否为图片文件"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        return Path(filename).suffix.lower() in image_extensions
    
    def extract_date_from_filename(self, filename):
        """从文件名提取日期（YYYYMMDD格式）"""
        # 标准格式：20250917_011946_953_a64c0437.jpeg
        match = re.match(r'^(\d{8})_', filename)
        if match:
            date_str = match.group(1)
            # 验证日期格式
            try:
                datetime.strptime(date_str, '%Y%m%d')
                return date_str
            except ValueError:
                pass
        return None
    
    def move_to_date_folder(self, file_path, date_folder):
        """移动文件到日期文件夹"""
        target_dir = self.base_dir / date_folder
        
        # 创建目录（如果不存在）
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            self.stats["folders_created"] += 1
            print(f"📁 创建文件夹: {date_folder}/")
        
        # 移动文件
        target_path = target_dir / file_path.name
        
        try:
            # 检查目标文件是否已存在
            if target_path.exists():
                print(f"⚠️  目标文件已存在，跳过: {file_path.name}")
                self.stats["skipped_files"] += 1
            else:
                shutil.move(str(file_path), str(target_path))
                self.stats["moved_files"] += 1
                self.file_moves.append({
                    "from": file_path.name,
                    "to": f"{date_folder}/{file_path.name}"
                })
                print(f"✅ 移动: {file_path.name} → {date_folder}/")
        except Exception as e:
            self.stats["errors"] += 1
            self.errors.append({
                "file": file_path.name,
                "error": str(e)
            })
            print(f"❌ 移动失败: {file_path.name} - {e}")
    
    def handle_special_file(self, file_path):
        """处理特殊命名的文件"""
        filename = file_path.name
        
        # 尝试从文件修改时间获取日期
        try:
            # 获取文件修改时间
            mtime = file_path.stat().st_mtime
            date_folder = datetime.fromtimestamp(mtime).strftime('%Y%m%d')
            
            print(f"🔍 特殊文件 '{filename}' 使用修改时间: {date_folder}")
            
            # 创建special子文件夹
            special_dir = self.base_dir / date_folder / "special"
            special_dir.mkdir(parents=True, exist_ok=True)
            
            # 移动到special子文件夹
            target_path = special_dir / filename
            
            if not target_path.exists():
                shutil.move(str(file_path), str(target_path))
                self.stats["special_files"] += 1
                self.special_files.append({
                    "file": filename,
                    "moved_to": f"{date_folder}/special/"
                })
                print(f"📦 特殊文件移动到: {date_folder}/special/")
            else:
                print(f"⚠️  特殊文件已存在，跳过: {filename}")
                self.stats["skipped_files"] += 1
                
        except Exception as e:
            self.stats["errors"] += 1
            self.errors.append({
                "file": filename,
                "error": str(e)
            })
            print(f"❌ 处理特殊文件失败: {filename} - {e}")
    
    def show_results(self):
        """显示整理结果"""
        print("\n" + "=" * 60)
        print("📊 整理完成！统计结果：")
        print("=" * 60)
        
        print(f"📁 文件总数: {self.stats['total_files']}")
        print(f"✅ 成功移动: {self.stats['moved_files']} 个文件")
        print(f"📦 特殊文件: {self.stats['special_files']} 个")
        print(f"⏭️  跳过文件: {self.stats['skipped_files']} 个")
        print(f"❌ 处理错误: {self.stats['errors']} 个")
        print(f"🆕 创建文件夹: {self.stats['folders_created']} 个")
        
        # 显示文件夹统计
        print("\n📂 文件夹分布:")
        folders = {}
        for move in self.file_moves:
            folder = move["to"].split("/")[0]
            folders[folder] = folders.get(folder, 0) + 1
        
        for folder in sorted(folders.keys()):
            print(f"  {folder}: {folders[folder]} 个文件")
        
        # 显示特殊文件
        if self.special_files:
            print("\n📦 特殊文件处理:")
            for special in self.special_files:
                print(f"  {special['file']} → {special['moved_to']}")
        
        # 显示错误
        if self.errors:
            print("\n⚠️ 错误列表:")
            for error in self.errors:
                print(f"  {error['file']}: {error['error']}")
        
        # 保存报告
        self.save_report()
    
    def save_report(self):
        """保存整理报告到JSON文件"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "statistics": self.stats,
            "file_moves": self.file_moves,
            "special_files": self.special_files,
            "errors": self.errors
        }
        
        report_file = self.base_dir.parent / "organize_report.json"
        
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n💾 详细报告已保存到: {report_file}")
        except Exception as e:
            print(f"\n⚠️ 保存报告失败: {e}")


def main():
    """主函数"""
    print("\n🖼️  图片整理工具 v1.0")
    print("该工具将把 downloaded_images 目录中的图片按日期整理到子文件夹中")
    print("-" * 60)
    
    # 询问用户确认
    print("\n⚠️  注意：此操作将移动文件到新的文件夹结构")
    print("    建议先备份 downloaded_images 文件夹")
    
    confirm = input("\n是否继续？(y/n): ").strip().lower()
    
    if confirm == 'y':
        organizer = ImageOrganizer()
        organizer.organize()
    else:
        print("❌ 操作已取消")


if __name__ == "__main__":
    main()