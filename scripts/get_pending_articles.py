#!/usr/bin/env python3
"""
获取待处理文章列表
按日期排序，新文章优先处理
"""

import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_manager import StatusManager
from github_utils import set_github_output


def main():
    parser = argparse.ArgumentParser(description='获取待处理文章（按日期排序，新文章优先）')
    parser.add_argument('--count', type=int, default=2, help='获取文章数量')
    parser.add_argument('--force', action='store_true', help='强制重新处理')
    args = parser.parse_args()
    
    manager = StatusManager()
    manager.initialize_from_raw_articles()
    
    # 获取待处理文章（已按日期降序排序）
    pending = manager.get_pending_articles(count=args.count, force=args.force)
    
    if pending:
        set_github_output('has_articles', 'true')
        print(f"\n📝 本次将处理 {len(pending)} 篇文章（按日期排序，新文章优先）:\n")
        
        for i, article in enumerate(pending, 1):
            date = article.get('source_date', '未知日期')
            print(f"  {i}. [{date}] {article['id']}")
        
        # 保存待处理列表到文件，供后续步骤使用
        with open('pending_articles.json', 'w', encoding='utf-8') as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
    else:
        set_github_output('has_articles', 'false')
        print("\n✅ 没有待处理的文章")
    
    # 显示统计信息
    stats = manager.get_statistics()
    print(f"\n📊 统计: 总计 {stats['total']} 篇 | 待处理 {stats['pending']} 篇 | 已完成 {stats['completed']} 篇")


if __name__ == "__main__":
    main()
