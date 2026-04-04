#!/usr/bin/env python3
"""
生成处理报告 - 生成 GitHub Actions Summary 报告
适配惠州仲恺中学 Hexo 博客
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_manager import StatusManager


def generate_report():
    """生成处理报告"""
    manager = StatusManager()
    stats = manager.get_statistics()
    
    report = f"""# 📝 惠州仲恺中学文章处理报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📊 处理统计

| 指标 | 数值 |
|------|------|
| 总文章数 | {stats['total']} |
| 已完成 | {stats['completed']} |
| 待处理 | {stats['pending']} |
| 处理中 | {stats['processing']} |
| 失败 | {stats['failed']} |
| 每日配额 | {stats['daily_limit']} |

**进度**: {stats['completed']}/{stats['total']} ({stats['completed']/max(stats['total'],1)*100:.1f}%)

```
进度条: [{'█' * int(stats['completed']/max(stats['total'],1)*20)}{'░' * (20-int(stats['completed']/max(stats['total'],1)*20))}]
```

---

## 📅 今日处理

"""
    
    # 读取今日处理的文章
    pending_path = Path("pending_articles.json")
    if pending_path.exists():
        with open(pending_path, 'r', encoding='utf-8') as f:
            pending = json.load(f)
        
        if pending:
            report += "### 本次处理的文章:\n\n"
            for article in pending:
                status = manager.status["articles"].get(article["id"], {}).get("status", "unknown")
                title = manager.status["articles"].get(article["id"], {}).get("title", article["id"])
                status_emoji = {"completed": "✅", "failed": "❌", "processing": "🔄"}.get(status, "❓")
                report += f"- {status_emoji} **{title}** ({article['id']})\n"
        else:
            report += "今日暂无处理任务。\n"
    else:
        report += "未找到处理记录。\n"
    
    # 读取质量检查报告
    quality_path = Path("quality_report.json")
    if quality_path.exists():
        with open(quality_path, 'r', encoding='utf-8') as f:
            quality_report = json.load(f)
        
        report += "\n---\n\n## 🔍 质量检查结果\n\n"
        
        total_issues = 0
        total_warnings = 0
        
        for result in quality_report:
            for check_name, check_result in result.get("checks", {}).items():
                total_issues += len(check_result.get("issues", []))
                total_warnings += len(check_result.get("warnings", []))
        
        if total_issues == 0 and total_warnings == 0:
            report += "✅ 所有文章质量良好，无错误或警告\n"
        else:
            if total_issues > 0:
                report += f"❌ 发现 **{total_issues}** 个错误需要修复\n"
            if total_warnings > 0:
                report += f"⚠️ 发现 **{total_warnings}** 个警告建议优化\n"
    
    # 添加最近处理历史
    report += "\n---\n\n## 📜 最近处理历史\n\n"
    
    recent_history = manager.status.get("history", [])[-10:]
    if recent_history:
        report += "| 时间 | 文章 | 状态 |\n"
        report += "|------|------|------|\n"
        for item in reversed(recent_history):
            status_emoji = "✅ 完成" if item["status"] == "completed" else "❌ 失败"
            article_title = manager.status["articles"].get(item.get("article_id", ""), {}).get("title", item.get("article_id", "未知"))
            report += f"| {item['date'][:16]} | {article_title} | {status_emoji} |\n"
    else:
        report += "_暂无历史记录_\n"
    
    # 添加预估时间
    remaining = stats['pending'] + stats['failed']
    if remaining > 0 and stats['daily_limit'] > 0:
        days = remaining / stats['daily_limit']
        report += f"\n---\n\n## ⏱️ 预计完成时间\n\n"
        report += f"按每日 {stats['daily_limit']} 篇的速度，还需 **{days:.0f} 天** 完成全部处理。\n"
        if remaining <= 100:
            report += f"\n预计完成日期: {(datetime.now() + __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d')}\n"
    
    # AI 提供商信息
    ai_provider = os.getenv('AI_PROVIDER', 'gemini')
    report += f"\n---\n\n## 🤖 AI 配置\n\n"
    report += f"- **提供商**: {ai_provider.upper()}\n"
    report += f"- **模型**: {os.getenv(f'{ai_provider.upper()}_MODEL', '默认')}\n"
    
    print(report)


if __name__ == "__main__":
    generate_report()
