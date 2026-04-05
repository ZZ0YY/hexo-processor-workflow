#!/usr/bin/env python3
"""
图片预分类脚本 - 在 AI 文本处理前对原始文章中的图片进行智能分类

流程：
  1. 读取 pending_articles.json（待处理文章列表）
  2. 对每篇原始文章提取图片 URL
  3. 使用 GLM 视觉模型分类：KEEP / DROP
  4. 将分类结果保存到 image_classification_results.json

输出格式：
  {
    "article_id": {
      "drop_urls": ["url1", "url2"],
      "keep_urls": ["url3", "url4"],
      "stats": { "total": N, "dropped": N, "kept": N }
    }
  }

后续 process_articles.py 会读取此结果，在发送给文本 AI 前移除 DROP 图片。
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from image_classifier import ImageClassifier
from github_utils import set_github_output


def extract_image_urls(content: str) -> list:
    """提取 Markdown 中的所有图片 URL（去重）"""
    urls = set()

    # Markdown 正文: ![alt](url)
    for url in re.findall(r'!\[[^\]]*\]\((http[^)]+)\)', content):
        urls.add(url.strip())

    # Front Matter cover 字段
    cover_match = re.search(
        r'^cover:\s*[\'"]?(https?://\S+?)[\'"]?\s*$', content, re.MULTILINE)
    if cover_match:
        url = cover_match.group(1).strip().strip("'\"")
        if url:
            urls.add(url)

    return list(urls)


def main():
    """主函数"""
    # 读取待处理文章列表
    pending_path = Path("pending_articles.json")
    if not pending_path.exists():
        set_github_output('classify_success', 'true')
        print("✅ 未找到待处理文章列表，跳过图片预分类")
        # 写入空结果，避免后续步骤报错
        with open('image_classification_results.json', 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return

    with open(pending_path, 'r', encoding='utf-8') as f:
        pending_articles = json.load(f)

    if not pending_articles:
        set_github_output('classify_success', 'true')
        print("✅ 没有待处理的文章，跳过图片预分类")
        with open('image_classification_results.json', 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return

    # 初始化分类器
    glm_key = os.getenv('GLM_API_KEY', '')
    if not glm_key:
        print("⚠️ GLM_API_KEY 未设置，跳过图片预分类（文本AI将自行处理图片）")
        set_github_output('classify_success', 'true')
        with open('image_classification_results.json', 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return

    classifier = ImageClassifier(glm_key)
    model = os.getenv('GLM_MODEL', 'glm-4.1v-thinking-flash')
    print(f"\n🖼️ 图片预分类开始（在文本AI处理前执行）")
    print(f"  🧠 视觉模型: {model}")
    print(f"  📄 文章数量: {len(pending_articles)}")
    print()

    results = {}
    total_dropped = 0
    total_kept = 0
    total_auto_dropped = 0
    total_images = 0

    for article in pending_articles:
        article_id = article["id"]
        source_path = article.get("source", "")

        if not source_path or not os.path.exists(source_path):
            print(f"  ⚠️ 跳过 {article_id[:50]}（源文件不存在）")
            continue

        # 读取原始文章
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"  ⚠️ 跳过 {article_id[:50]}（读取失败: {e}）")
            continue

        # 提取图片 URL
        urls = extract_image_urls(content)

        if not urls:
            print(f"  📄 {article_id[:50]}... — 无图片")
            results[article_id] = {
                "drop_urls": [],
                "keep_urls": [],
                "stats": {"total": 0, "dropped": 0, "kept": 0}
            }
            continue

        # 调用视觉模型分类
        print(f"  📄 {article_id[:50]}... — 发现 {len(urls)} 张图片，开始分类...")
        decisions, downloads = classifier.classify_article_images(urls)

        drop_urls = [url for url, (d, _) in decisions.items() if d == "DROP"]
        keep_urls = [url for url, (d, _) in decisions.items() if d == "KEEP"]

        results[article_id] = {
            "drop_urls": drop_urls,
            "keep_urls": keep_urls,
            "stats": {
                "total": len(urls),
                "dropped": len(drop_urls),
                "kept": len(keep_urls)
            }
        }

        total_images += len(urls)
        total_dropped += len(drop_urls)
        total_kept += len(keep_urls)
        total_auto_dropped += classifier.stats.get("auto_dropped", 0)

        if drop_urls:
            print(f"    🗑️ 移除 {len(drop_urls)} 张装饰图，保留 {len(keep_urls)} 张内容图")
        else:
            print(f"    ✅ 全部 {len(keep_urls)} 张图片均为内容图，予以保留")

    # 保存分类结果
    with open('image_classification_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 打印汇总统计
    print()
    print("=" * 55)
    print(f"📊 图片预分类完成")
    print(f"  📄 处理文章: {len(results)} 篇")
    print(f"  🖼️ 图片总计: {total_images} 张")
    print(f"  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─")
    print(f"  🗑️ 标记删除: {total_dropped} 张装饰图")
    print(f"    ├ .gif/.svg 预删: {total_auto_dropped} 张")
    print(f"    └ AI 判定删除: {total_dropped - total_auto_dropped} 张")
    print(f"  ✅ 标记保留: {total_kept} 张内容图")
    if total_images > 0:
        print(f"  📊 节省率: {total_dropped}/{total_images} 张"
              f"（{total_dropped / total_images * 100:.0f}%）")
    print("=" * 55)
    print()
    print("💡 后续流程：文本AI将基于此分类结果处理文章，仅保留内容图片")

    set_github_output('classify_success', 'true')
    classify_stats = (
        f"- 📄 处理文章: {len(results)} 篇\n"
        f"- 🗑️ 删除装饰图: {total_dropped} 张\n"
        f"- ✅ 保留内容图: {total_kept} 张\n"
        f"- 📊 节省率: {total_dropped}/{max(total_images, 1)} 张"
        f"（{total_dropped / max(total_images, 1) * 100:.0f}%）"
    )
    set_github_output('classify_stats', classify_stats)


if __name__ == "__main__":
    main()
