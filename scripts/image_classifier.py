#!/usr/bin/env python3
"""
图片智能分类模块 - 使用 GLM-4.1V-Thinking-Flash 视觉模型
识别微信公众号文章中的装饰性图片，辅助图片迁移决策。

使用方式：
  1. 作为模块被 image_migrator.py 导入调用
  2. 也可独立运行：python image_classifier.py

分类策略：
  - .gif/.svg 后缀 → 直接 DROP（无需调用 AI）
  - 其他图片 → 下载到内存 → base64 发送 GLM → KEEP/DROP
  - 分类失败 → 默认 KEEP（保守策略）
"""

import base64
import json
import os
import re
import sys
import time
import requests
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ==================== 配置 ====================
GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = os.getenv('GLM_MODEL', 'glm-4.1v-thinking-flash')
MAX_CONCURRENCY = 5   # GLM-4.1V 并发限制数
MAX_RETRIES = 2        # 每张图片最大重试次数
RETRY_DELAY = 3        # 重试间隔（秒）

# 必定删除的后缀（装饰性）
ALWAYS_DROP_EXTENSIONS = {'.gif', '.svg'}

# 下载超时
DOWNLOAD_TIMEOUT = 20

# ==================== 分类提示词 ====================
CLASSIFICATION_PROMPT = """你是一个专业的微信公众号文章图片分析师。请分析这张图片，判断它是否应该保留在学校官网文章中。

**应该删除 [DROP] 的情况：**
- 微信公众号装饰性分割线
- 关注引导图、二维码底图
- 纯色或渐变背景banner（无实际文字或场景信息）
- 气氛渲染图（心形、星星、灯笼、花朵、飘带等纯装饰元素）
- 矢量图标、简单平面素材、emoji大图
- 品牌水印logo（非文章主题内容）
- 仅用于排版美化的无信息量图片
- 重复出现的相同或相似装饰图

**应该保留 [KEEP] 的情况：**
- 校园活动现场照片（表彰大会、运动会、开学典礼、文艺演出等）
- 师生合影、个人照片、领导讲话照片
- 教学场景、课堂实拍、实验照片
- 奖状、证书、成绩展示、荣誉牌匾
- 校园环境、建筑、设施实景照片
- 包含具体文字信息（标题、通知、数据图表）的图片
- 新闻事件的纪实照片
- 学生作品展示、社团活动照片

严格只输出如下JSON格式，不要输出任何其他文字：
{"decision":"KEEP","reason":"简要原因"}
或
{"decision":"DROP","reason":"简要原因"}"""


class ImageClassifier:
    """图片分类器 - 使用 GLM-4.1V 视觉模型（base64 本地图片）"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.stats = {
            "total": 0,
            "auto_dropped": 0,   # .gif/.svg 预过滤
            "keep": 0,
            "drop": 0,
            "error": 0
        }

    def _download_image(self, url: str) -> Optional[bytes]:
        """下载图片到内存"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://mp.weixin.qq.com/'
            }
            resp = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            print(f"      ⚠️ 下载失败: {e}")
            return None

    def _get_url_extension(self, url: str) -> str:
        """提取 URL 的图片后缀"""
        # 去除查询参数
        clean_url = url.split('?')[0].split('#')[0]
        ext = Path(clean_url).suffix.lower()
        return ext

    def classify_single(self, image_bytes: bytes) -> Tuple[str, str]:
        """分类单张图片（传入原始 bytes）

        Returns:
            (decision, reason)  decision: "KEEP" / "DROP"
        """
        b64 = base64.b64encode(image_bytes).decode('utf-8')

        # 简单的格式推断
        if image_bytes[:4] == b'\x89PNG':
            mime = "image/png"
        elif image_bytes[:2] == b'\xff\xd8':
            mime = "image/jpeg"
        elif image_bytes[:4] == b'RIFF':
            mime = "image/webp"
        else:
            mime = "image/jpeg"  # 默认

        payload = {
            "model": GLM_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": CLASSIFICATION_PROMPT}
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 500
        }

        last_error = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = requests.post(
                    GLM_API_URL, headers=self.headers,
                    json=payload, timeout=30
                )
                response.raise_for_status()
                result = response.json()

                message = result["choices"][0]["message"]
                content = message.get("content", "")

                # GLM thinking 模型的思考内容在 reasoning_content 字段中
                # content 是最终回答，不需要额外清理
                # 但以防万一，清理可能的思考标签
                content = re.sub(r'\[#S\].*?\[/S\]', '', content, flags=re.DOTALL).strip()

                # 提取 JSON
                json_match = re.search(r'\{[^}]+\}', content)
                if json_match:
                    parsed = json.loads(json_match.group())
                    decision = str(parsed.get("decision", "KEEP")).upper()
                    reason = parsed.get("reason", "")

                    if decision not in ("KEEP", "DROP"):
                        decision = "KEEP"

                    return decision, reason

                # JSON 解析失败，从文本推断
                if "DROP" in content.upper() and "KEEP" not in content.upper():
                    return "DROP", content[:80]
                return "KEEP", content[:80]

            except requests.exceptions.Timeout:
                last_error = "请求超时"
            except requests.exceptions.HTTPError as e:
                body = e.response.text[:100] if e.response else ""
                last_error = f"HTTP {e.response.status_code} {body}"
            except json.JSONDecodeError:
                last_error = "JSON解析失败"
            except Exception as e:
                last_error = str(e)[:60]

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        # 所有重试失败 → 默认保留
        return "KEEP", f"分类失败({last_error})，默认保留"

    def classify_article_images(
        self,
        urls: List[str]
    ) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, bytes]]:
        """分类一篇文章中的所有图片

        流程：
        1. 预过滤 .gif/.svg → 直接 DROP
        2. 下载剩余图片到内存
        3. 并发发送给 GLM 分类
        4. 返回每张图片的决策

        Args:
            urls: 图片 URL 列表

        Returns:
            decisions: {url: (decision, reason)}
            downloads: {url: image_bytes}  （保留的图片数据，供后续上传用）
        """
        decisions = {}
        downloads = {}

        # --- 第一轮：预过滤 .gif/.svg ---
        filter_urls = []
        for url in urls:
            ext = self._get_url_extension(url)
            if ext in ALWAYS_DROP_EXTENSIONS:
                decisions[url] = ("DROP", f"装饰性格式({ext})")
                self.stats["auto_dropped"] += 1
                print(f"      🗑️  [DROP] {ext.upper()} {url[:50]}...")
            else:
                filter_urls.append(url)

        if not filter_urls:
            self.stats["total"] = len(urls)
            return decisions, downloads

        # --- 第二轮：下载图片到内存 ---
        print(f"      📥 下载 {len(filter_urls)} 张图片...")

        url_bytes_map = {}  # {url: bytes}
        for url in filter_urls:
            img_bytes = self._download_image(url)
            if img_bytes:
                url_bytes_map[url] = img_bytes
            else:
                # 下载失败 → 默认保留（保守策略）
                decisions[url] = ("KEEP", "下载失败，默认保留")
                self.stats["error"] += 1
                print(f"      ⚠️  {url[:50]}... → 下载失败，默认保留")

        if not url_bytes_map:
            self.stats["total"] = len(urls)
            return decisions, downloads

        # --- 第三轮：并发发送 base64 给 GLM 分类 ---
        print(f"      🧠 GLM 分类中（并发 {MAX_CONCURRENCY}）...")

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
            future_to_url = {
                executor.submit(self.classify_single, img_bytes): url
                for url, img_bytes in url_bytes_map.items()
            }

            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    decision, reason = future.result()
                    decisions[url] = (decision, reason)
                    short_url = url[:50] + "..." if len(url) > 50 else url

                    if decision == "KEEP":
                        self.stats["keep"] += 1
                        downloads[url] = url_bytes_map[url]
                        print(f"      ✅ [KEEP] {short_url}")
                    elif decision == "DROP":
                        self.stats["drop"] += 1
                        print(f"      🗑️  [DROP] {short_url} — {reason}")
                    else:
                        self.stats["error"] += 1
                        downloads[url] = url_bytes_map[url]
                        print(f"      ⚠️  [????] {short_url} — {reason}")
                except Exception as e:
                    # 异常 → 默认保留
                    decisions[url] = ("KEEP", f"异常({e})，默认保留")
                    downloads[url] = url_bytes_map[url]
                    self.stats["error"] += 1

        self.stats["total"] = len(urls)
        return decisions, downloads


def remove_images_from_content(content: str, drop_urls: set) -> str:
    """从 Markdown 内容中移除指定 URL 的图片

    - 纯图片行 → 整行删除
    - 图片嵌入文字中 → 只移除图片语法
    - cover 字段 → 留空（保留字段）
    """
    lines = content.split('\n')
    new_lines = []

    for line in lines:
        # 匹配本行所有 Markdown 图片
        md_images = re.findall(r'!\[[^\]]*\]\((http[^)]+)\)', line)

        has_drop = False
        for _, img_url in md_images:
            if img_url.strip() in drop_urls:
                has_drop = True
                break

        if not has_drop:
            new_lines.append(line)
            continue

        # 纯图片行（只有图片语法和空白）→ 整行删除
        stripped = line.strip()
        is_image_only = bool(re.match(r'^!\[.*?\]\(http[^)]+\)$', stripped))

        if is_image_only:
            continue
        else:
            # 图片嵌入文字中 → 只移除 DROP 的图片
            for _, img_url in md_images:
                if img_url.strip() in drop_urls:
                    line = re.sub(
                        r'!\[[^\]]*\]\(' + re.escape(img_url.strip()) + r'\)',
                        '', line
                    )
            new_lines.append(line)

    result = '\n'.join(new_lines)

    # 处理 cover 字段：将被 DROP 的 cover 留空
    for drop_url in drop_urls:
        result = re.sub(
            r"(cover:\s*)[\"']?" + re.escape(drop_url) + r"[\"']?",
            r'\1""',
            result
        )

    # 清理过多连续空行
    result = re.sub(r'\n{4,}', '\n\n\n', result)
    return result
