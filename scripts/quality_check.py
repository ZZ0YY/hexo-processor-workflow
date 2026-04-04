#!/usr/bin/env python3
"""
质量检查脚本 - 验证处理后的文章质量
适配惠州仲恺中学 Hexo 博客规范
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# 允许的分类列表
VALID_CATEGORIES = [
    "新闻动态", "校园新闻", "通知公告", "教务动态",
    "校园活动", "课程教学", "师资力量", "办学成果", "荣誉时刻"
]


def check_front_matter(content: str) -> Dict:
    """检查 Front Matter 完整性和规范性"""
    issues = []
    warnings = []
    
    # 检查是否有 Front Matter
    if not content.startswith('---'):
        issues.append("❌ 缺少 Front Matter")
        return {"valid": False, "issues": issues, "warnings": warnings}
    
    # 提取 Front Matter
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        issues.append("❌ Front Matter 格式错误")
        return {"valid": False, "issues": issues, "warnings": warnings}
    
    front_matter = match.group(1)
    
    # 检查必要字段
    required_fields = {
        'title': '标题',
        'date': '日期',
        'author': '作者',
        'categories': '分类',
        'tags': '标签',
        'excerpt': '摘要'
    }
    
    for field, name in required_fields.items():
        if field not in front_matter:
            issues.append(f"❌ 缺少必要字段: {name} ({field})")
    
    # 检查标题长度
    title_match = re.search(r'^title:\s*(.+)$', front_matter, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
        if len(title) < 20:
            warnings.append(f"⚠️ 标题较短（{len(title)}字），建议20-30字")
        elif len(title) > 35:
            warnings.append(f"⚠️ 标题过长（{len(title)}字），建议20-30字")
    
    # 检查日期格式
    date_match = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', front_matter, re.MULTILINE)
    if not date_match:
        date_match = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2})', front_matter, re.MULTILINE)
        if date_match:
            warnings.append("⚠️ 日期格式不完整，建议使用 YYYY-MM-DD HH:mm:ss")
        else:
            issues.append("❌ 日期格式错误")
    
    # 检查作者
    author_match = re.search(r'^author:\s*(.+)$', front_matter, re.MULTILINE)
    if author_match:
        author = author_match.group(1).strip()
        if author != "惠州仲恺中学":
            warnings.append(f'⚠️ 作者应为\'惠州仲恺中学\'，当前为\'{author}\'')
    
    # 检查分类是否在允许列表中
    category_match = re.search(r'^categories:\s*\n(\s+-\s+.+\n?)+', front_matter, re.MULTILINE)
    if category_match:
        categories_text = category_match.group(0)
        found_categories = re.findall(r'-\s+(.+)', categories_text)
        for cat in found_categories:
            cat = cat.strip()
            if cat not in VALID_CATEGORIES:
                issues.append(f'❌ 无效分类: \'{cat}\'，必须从分类体系中选择')
    else:
        issues.append("❌ 分类格式错误或为空")
    
    # 检查标签数量
    tags_match = re.search(r'^tags:\s*\n(\s+-\s+.+\n?)+', front_matter, re.MULTILINE)
    if tags_match:
        tags_text = tags_match.group(0)
        tags = re.findall(r'-\s+(.+)', tags_text)
        if len(tags) < 3:
            warnings.append(f"⚠️ 标签数量较少（{len(tags)}个），建议3-5个")
        elif len(tags) > 6:
            warnings.append(f"⚠️ 标签数量较多（{len(tags)}个），建议3-5个")
    else:
        issues.append("❌ 标签格式错误或为空")
    
    # 检查摘要长度
    excerpt_match = re.search(r'^excerpt:\s*(.+?)(?=\n\w|\n---|\Z)', front_matter, re.DOTALL | re.MULTILINE)
    if excerpt_match:
        excerpt = excerpt_match.group(1).strip()
        if len(excerpt) < 100:
            warnings.append(f"⚠️ 摘要较短（{len(excerpt)}字），建议120-150字")
        elif len(excerpt) > 200:
            warnings.append(f"⚠️ 摘要过长（{len(excerpt)}字），建议120-150字")
    else:
        issues.append("❌ 缺少摘要")
    
    # 检查 cover 字段
    cover_match = re.search(r'^cover:\s*(.+)$', front_matter, re.MULTILINE)
    if cover_match:
        cover = cover_match.group(1).strip()
        if cover and not cover.startswith('http'):
            warnings.append("⚠️ cover 图片链接可能无效（非 http 开头）")
    
    return {"valid": len(issues) == 0, "issues": issues, "warnings": warnings}


def check_content_structure(content: str) -> Dict:
    """检查内容结构"""
    issues = []
    warnings = []
    
    # 移除 Front Matter 后的内容
    main_content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
    
    # 检查内容长度
    if len(main_content.strip()) < 300:
        issues.append(f"❌ 内容过短（{len(main_content.strip())}字符），可能被错误缩减")
    
    # 检查是否有标题结构
    headings = re.findall(r'^#{2,3}\s+(.+)$', main_content, re.MULTILINE)
    if len(headings) < 2:
        warnings.append(f"⚠️ 缺少小标题结构（仅{len(headings)}个标题），建议结构化内容")
    
    # 检查是否有过多连续空行
    if re.search(r'\n{4,}', main_content):
        warnings.append("⚠️ 存在过多连续空行")
    
    # 检查结尾
    if "本文章来自惠州仲恺中学公众号" not in main_content:
        warnings.append("⚠️ 缺少标准结尾：本文章来自惠州仲恺中学公众号")
    
    return {"valid": len(issues) == 0, "issues": issues, "warnings": warnings}


def check_formatting(content: str) -> Dict:
    """检查格式问题"""
    issues = []
    warnings = []
    
    # 检查是否有未转换的微信格式
    wechat_patterns = [
        (r'>\s*作者[：:]', "可能包含微信作者信息"),
        (r'>\s*来源[：:]', "可能包含微信来源信息"),
        (r'点击上方.*?关注', "可能包含微信关注引导"),
        (r'扫描.*?二维码', "可能包含二维码描述"),
        (r'[═━]{3,}', "可能包含装饰性分割线"),
    ]
    
    for pattern, message in wechat_patterns:
        if re.search(pattern, content):
            warnings.append(f"⚠️ {message}")
    
    # 检查图片 alt 文本
    images = re.findall(r'!\[(.*?)\]\((.*?)\)', content)
    for alt, url in images:
        # 检查 alt 是否有意义
        if not alt or alt in ['图片', 'image', 'img']:
            warnings.append(f"⚠️ 图片缺少描述性 alt 文本: {url[:30]}...")
        # 检查 alt 是否包含关键词
        elif '仲恺' not in alt and '中学' not in alt and '学生' not in alt and '教师' not in alt:
            # 这不是错误，只是提示
            pass
    
    # 检查相对路径图片
    broken_images = [url for alt, url in images if not url.startswith(('http', 'https', '/'))]
    if broken_images:
        issues.append(f"❌ 存在 {len(broken_images)} 个相对路径图片")
    
    return {"valid": len(issues) == 0, "issues": issues, "warnings": warnings}


def check_article(file_path: str) -> Dict:
    """检查单篇文章"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    front_matter_result = check_front_matter(content)
    structure_result = check_content_structure(content)
    formatting_result = check_formatting(content)
    
    all_valid = (
        front_matter_result["valid"] and 
        structure_result["valid"] and 
        formatting_result["valid"]
    )
    
    return {
        "file": file_path,
        "valid": all_valid,
        "checks": {
            "front_matter": front_matter_result,
            "structure": structure_result,
            "formatting": formatting_result
        }
    }


def print_report(results: List[Dict]):
    """打印检查报告"""
    print("\n" + "="*60)
    print("📋 质量检查报告")
    print("="*60)
    
    total_issues = 0
    total_warnings = 0
    
    for result in results:
        file_name = Path(result["file"]).name
        
        print(f"\n📄 {file_name}")
        
        if result["valid"]:
            print("  ✅ 通过所有检查")
        else:
            for check_name, check_result in result["checks"].items():
                # 打印错误
                for issue in check_result.get("issues", []):
                    print(f"  {issue}")
                    total_issues += 1
                # 打印警告
                for warning in check_result.get("warnings", []):
                    print(f"  {warning}")
                    total_warnings += 1
    
    print("\n" + "-"*60)
    print(f"📊 检查统计:")
    print(f"  - 检查文件: {len(results)}")
    print(f"  - 错误数量: {total_issues}")
    print(f"  - 警告数量: {total_warnings}")
    print("-"*60)
    
    if total_issues > 0:
        print("\n⚠️ 发现错误，建议修复后再合并 PR")
    elif total_warnings > 0:
        print("\n✅ 无严重错误，但有一些建议优化项")
    else:
        print("\n✅ 所有文章质量良好")


def main():
    """主函数"""
    processed_dir = Path("processed")
    if not processed_dir.exists():
        print("没有已处理的文章目录")
        return
    
    # 获取最近处理的文章
    recent_files = sorted(
        processed_dir.glob("*.md"), 
        key=lambda p: p.stat().st_mtime, 
        reverse=True
    )[:10]  # 检查最近10篇
    
    if not recent_files:
        print("没有已处理的文章")
        return
    
    results = []
    for file_path in recent_files:
        result = check_article(str(file_path))
        results.append(result)
    
    print_report(results)
    
    # 保存检查结果
    report_path = Path("quality_report.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细报告已保存到: {report_path}")


if __name__ == "__main__":
    main()
