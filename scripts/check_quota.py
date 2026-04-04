#!/usr/bin/env python3
"""
配额检查脚本 - 检查今日处理配额
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_manager import StatusManager
from github_utils import set_github_output


def main():
    manager = StatusManager()
    manager.initialize_from_raw_articles()
    
    if manager.check_daily_quota():
        set_github_output('can_process', 'true')
        print("✅ 今日仍有处理配额")
    else:
        set_github_output('can_process', 'false')
        print("⚠️ 今日处理配额已用完")
    
    stats = manager.get_statistics()
    print(f"\n📊 统计信息:")
    print(f"  - 总文章数: {stats['total']}")
    print(f"  - 待处理: {stats['pending']}")
    print(f"  - 已完成: {stats['completed']}")
    print(f"  - 失败: {stats['failed']}")


if __name__ == "__main__":
    main()
