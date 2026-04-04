#!/usr/bin/env python3
"""
状态管理器 - 管理 Hexo 文章处理状态
支持从文件名提取日期并按日期排序
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


class StatusManager:
    """文章处理状态管理器"""
    
    def __init__(self, status_file: str = "status.json"):
        self.status_file = Path(status_file)
        self.status = self._load_status()
    
    def _load_status(self) -> Dict[str, Any]:
        """加载状态文件"""
        if self.status_file.exists():
            with open(self.status_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 初始化默认状态
        return {
            "total": 0,
            "processed": 0,
            "failed": 0,
            "last_processed": None,
            "daily_limit": int(os.getenv('DAILY_LIMIT', '2')),
            "history": [],  # 处理历史记录
            "articles": {}
        }
    
    def save(self):
        """保存状态文件"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump(self.status, f, ensure_ascii=False, indent=2)
    
    def extract_date_from_filename(self, filename: str) -> Optional[str]:
        """从文件名提取日期
        
        支持格式：
        - [2026-01-26]标题.md
        - 2026-01-26-标题.md
        - 20260126标题.md
        """
        # 格式1: [2026-01-26]
        match = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', filename)
        if match:
            return match.group(1)
        
        # 格式2: 2026-01-26-开头
        match = re.search(r'^(\d{4}-\d{2}-\d{2})', filename)
        if match:
            return match.group(1)
        
        # 格式3: 20260126
        match = re.search(r'^(\d{4})(\d{2})(\d{2})', filename)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        
        return None
    
    def initialize_from_raw_articles(self, raw_dir: str = "raw-articles"):
        """从原始文章目录初始化状态，按日期排序（新文章优先）"""
        raw_path = Path(raw_dir)
        if not raw_path.exists():
            print(f"Warning: {raw_dir} does not exist")
            return
        
        existing_ids = set(self.status["articles"].keys())
        new_articles = {}
        
        # 收集所有文件及其日期
        file_list = []
        for md_file in raw_path.glob("*.md"):
            article_id = md_file.stem
            if article_id not in existing_ids:
                date_str = self.extract_date_from_filename(md_file.name)
                file_list.append({
                    "path": md_file,
                    "id": article_id,
                    "date": date_str or "1970-01-01"  # 无日期的放最后
                })
        
        # 按日期降序排序（新文章在前）
        file_list.sort(key=lambda x: x["date"], reverse=True)
        
        # 按排序顺序添加文章
        for item in file_list:
            new_articles[item["id"]] = {
                "id": item["id"],
                "source": str(item["path"]),
                "source_date": item["date"] if item["date"] != "1970-01-01" else None,
                "status": "pending",
                "processed_at": None,
                "output": None,
                "title": None,
                "error": None,
                "attempts": 0,
                "images_migrated": False  # 图片是否已迁移
            }
        
        if new_articles:
            self.status["articles"].update(new_articles)
            self.status["total"] = len(self.status["articles"])
            self.save()
            print(f"Added {len(new_articles)} new articles to tracking (sorted by date, newest first)")
    
    def get_pending_articles(self, count: int = 2, force: bool = False) -> List[Dict[str, Any]]:
        """获取待处理的文章（按日期排序，新文章优先）
        
        Args:
            count: 获取文章数量
            force: 是否强制重新处理已完成的文章
        """
        pending = []
        
        if force:
            # 强制模式：包含所有文章（包括已完成的）
            for article_id, article in self.status["articles"].items():
                pending.append(article)
        else:
            # 收集所有待处理文章
            for article_id, article in self.status["articles"].items():
                if article["status"] == "pending" and article["attempts"] < 3:
                    pending.append(article)
            
            # 如果没有待处理的，检查失败但可重试的
            if not pending:
                for article_id, article in self.status["articles"].items():
                    if article["status"] == "failed" and article["attempts"] < 3:
                        pending.append(article)
        
        # 按源文件日期排序（新文章优先）
        def get_sort_key(article):
            date = article.get("source_date")
            if date:
                try:
                    return datetime.strptime(date, "%Y-%m-%d")
                except:
                    pass
            return datetime.min
        
        pending.sort(key=get_sort_key, reverse=True)
        
        # 返回指定数量
        return pending[:count]
    
    def mark_processing(self, article_id: str):
        """标记文章为处理中"""
        if article_id in self.status["articles"]:
            self.status["articles"][article_id]["status"] = "processing"
            self.save()
    
    def mark_completed(self, article_id: str, output_path: str, title: str = None):
        """标记文章处理完成"""
        if article_id in self.status["articles"]:
            article = self.status["articles"][article_id]
            article["status"] = "completed"
            article["processed_at"] = datetime.now().isoformat()
            article["output"] = output_path
            article["attempts"] += 1
            if title:
                article["title"] = title
            
            self.status["processed"] = sum(
                1 for a in self.status["articles"].values() 
                if a["status"] == "completed"
            )
            self.status["last_processed"] = datetime.now().strftime("%Y-%m-%d")
            
            # 添加到历史记录
            self.status["history"].append({
                "date": datetime.now().isoformat(),
                "article_id": article_id,
                "status": "completed"
            })
            
            self.save()
    
    def mark_images_migrated(self, article_id: str):
        """标记文章图片已迁移"""
        if article_id in self.status["articles"]:
            self.status["articles"][article_id]["images_migrated"] = True
            self.save()
    
    def mark_failed(self, article_id: str, error: str):
        """标记文章处理失败"""
        if article_id in self.status["articles"]:
            article = self.status["articles"][article_id]
            article["status"] = "failed"
            article["error"] = error
            article["attempts"] += 1
            
            self.status["failed"] = sum(
                1 for a in self.status["articles"].values() 
                if a["status"] == "failed"
            )
            
            # 添加到历史记录
            self.status["history"].append({
                "date": datetime.now().isoformat(),
                "article_id": article_id,
                "status": "failed",
                "error": error
            })
            
            self.save()
    
    def check_daily_quota(self) -> bool:
        """检查今日是否还有处理配额"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_processed = sum(
            1 for h in self.status["history"]
            if h["date"].startswith(today) and h["status"] == "completed"
        )
        
        return today_processed < self.status["daily_limit"]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total": self.status["total"],
            "pending": sum(1 for a in self.status["articles"].values() if a["status"] == "pending"),
            "processing": sum(1 for a in self.status["articles"].values() if a["status"] == "processing"),
            "completed": self.status["processed"],
            "failed": self.status["failed"],
            "last_processed": self.status["last_processed"],
            "daily_limit": self.status["daily_limit"]
        }


if __name__ == "__main__":
    # 测试代码
    manager = StatusManager()
    manager.initialize_from_raw_articles()
    print(json.dumps(manager.get_statistics(), indent=2))
    
    # 显示待处理文章列表
    pending = manager.get_pending_articles(5)
    print("\n待处理文章（按日期排序）:")
    for article in pending:
        print(f"  - {article.get('source_date', '无日期')}: {article['id']}")
