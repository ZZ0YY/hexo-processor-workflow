#!/usr/bin/env python3
"""
图片迁移脚本（集成视觉分类）
将微信图片经过智能分类后，转换为 WebP 并上传到图床。

每篇文章处理流程（串行）：
  1. 提取所有图片 URL
  2. 预过滤 .gif/.svg → 直接从 MD 删除
  3. 下载剩余图片到内存
  4. 并发发送 base64 给 GLM 分类（≤5路）
  5. 分类完成 → 从 MD 删除 DROP 图片
  6. 上传 KEEP 图片到图床（WebP 转换）
  7. 替换 URL → 写回文件
  8. 该篇文章处理完成
"""

import os
import re
import sys
import json
import time
import shutil
import requests
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ Pillow 未安装，将跳过 WebP 转换")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from github_utils import set_github_output
from image_classifier import ImageClassifier, remove_images_from_content


class WebPMigrator:
    """图片迁移器（集成智能分类）"""

    def __init__(self, enable_classification: bool = True):
        # ---- 图床配置 ----
        self.host_url = (os.getenv('IMAGE_HOST_URL', '') or
                         'https://photo.20080601.xyz')
        self.display_url = (os.getenv('IMAGE_DISPLAY_URL', '') or
                            'https://photo1.20080601.xyz')
        self.api_token = os.getenv('IMAGE_API_TOKEN', '') or ''
        self.upload_channel = os.getenv('IMAGE_UPLOAD_CHANNEL', '') or 'telegram'
        self.target_folder = os.getenv('IMAGE_TARGET_FOLDER', '') or 'wx'
        self.upload_api_url = f"{self.host_url}/upload"
        self.cache_file = "image_migration_cache.json"

        # ---- 分类器 ----
        self.enable_classification = enable_classification
        self.classifier = None
        if enable_classification:
            glm_key = os.getenv('GLM_API_KEY', '')
            if glm_key:
                self.classifier = ImageClassifier(glm_key)
                print(f"  🧠 图片分类已启用（{os.getenv('GLM_MODEL', 'glm-4.1v-thinking-flash')}）")
            else:
                print("  ⚠️ GLM_API_KEY 未设置，图片分类已跳过")

        # ---- 统计 ----
        self.stats = {
            'total_files': 0,
            'total_images': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'converted': 0,
            # 分类相关
            'classification_enabled': self.classifier is not None,
            'auto_dropped': 0,     # .gif/.svg 预过滤
            'ai_dropped': 0,       # GLM 判定删除
            'ai_kept': 0,          # GLM 判定保留
            'ai_error': 0,         # GLM 分类失败
        }
        self.failed_records = []
        self.global_url_map = self._load_cache()

        # ---- 验证 ----
        if not self.host_url:
            print("⚠️ IMAGE_HOST_URL 未配置")
        if not self.api_token:
            print("⚠️ IMAGE_API_TOKEN 未配置")

    # ==================== 缓存 ====================

    def _load_cache(self) -> Dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.global_url_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"    [!] 缓存保存失败: {e}")

    # ==================== 工具方法 ====================

    def _request_with_retry(self, method: str, url: str, **kwargs):
        for attempt in range(3):
            try:
                if method == 'GET':
                    resp = requests.get(url, **kwargs)
                else:
                    resp = requests.post(url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise

    def convert_to_webp(self, image_data: bytes) -> Optional[bytes]:
        if not PIL_AVAILABLE:
            return None
        try:
            img = Image.open(BytesIO(image_data))
            if img.mode == 'CMYK':
                img = img.convert('RGB')
            buf = BytesIO()
            img.save(buf, format='WEBP', quality=80, method=4)
            return buf.getvalue()
        except Exception as e:
            print(f"    ⚠️ WebP 转换失败: {e}")
            return None

    def upload_to_host(self, image_data: bytes, filename: str,
                       content_type: str) -> Optional[str]:
        try:
            headers = {"Authorization": f"Bearer {self.api_token}"}
            params = {
                'uploadChannel': self.upload_channel,
                'uploadFolder': self.target_folder
            }
            files = {'file': (filename, image_data, content_type)}

            resp = self._request_with_retry(
                'POST', self.upload_api_url,
                files=files, headers=headers,
                params=params, timeout=30
            )
            result = resp.json()

            if isinstance(result, list) and len(result) > 0 and 'src' in result[0]:
                src = result[0]['src']
                final_url = src if src.startswith('http') else f"{self.host_url}{src}"
                final_url = final_url.replace(self.host_url, self.display_url)
                return final_url
            return None
        except Exception as e:
            print(f"    上传失败: {e}")
            return None

    def _extract_all_urls(self, content: str) -> List[str]:
        """提取文章中所有图片 URL（去重）"""
        url_set = set()

        # Markdown 正文: ![alt](url)
        for url in re.findall(r'!\[[^\]]*\]\((http[^)]+)\)', content):
            url_set.add(url.strip())

        # Front Matter cover
        cover_match = re.search(
            r'^cover:\s*[\'"]?(https?://\S+?)[\'"]?\s*$', content, re.MULTILINE)
        if cover_match:
            url_set.add(cover_match.group(1).strip().strip(chr(39) + chr(34)))

        return list(url_set)

    def _download_image(self, url: str) -> Optional[bytes]:
        """下载图片"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://mp.weixin.qq.com/'
            }
            resp = self._request_with_retry('GET', url, headers=headers, timeout=20)
            return resp.content
        except Exception as e:
            return None

    # ==================== 核心流程 ====================

    def process_file(self, file_path: str) -> bool:
        """处理单篇文章（分类 → 上传 → 完成）"""
        self.stats['total_files'] += 1

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"  ❌ 无法读取文件: {e}")
            return False

        # ---- Step 1: 提取所有图片 URL ----
        all_urls = self._extract_all_urls(content)

        if not all_urls:
            print(f"    📷 无图片")
            return True

        # 排除已上传的
        pending_urls = [
            u for u in all_urls
            if self.host_url not in u and self.display_url not in u
        ]

        if not pending_urls:
            print(f"    📷 所有图片已在图床（缓存命中）")
            # 仍需替换缓存中的 URL
            new_content = content
            for old, new in self.global_url_map.items():
                new_content = new_content.replace(old, new)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True

        print(f"    📷 发现 {len(pending_urls)} 张图片")

        # ---- Step 2: 智能分类（可选）----
        keep_urls = list(pending_urls)  # 默认全部保留
        drop_urls = set()
        classified_downloads = {}       # 分类阶段下载的图片数据（复用）

        if self.classifier:
            print(f"    🧠 开始智能分类...")
            decisions, classified_downloads = self.classifier.classify_article_images(
                pending_urls)

            keep_urls = []
            drop_urls = set()

            for url, (decision, reason) in decisions.items():
                if decision == "DROP":
                    drop_urls.add(url)
                elif decision == "KEEP":
                    keep_urls.append(url)

            # 汇总分类统计
            self.stats['auto_dropped'] = self.classifier.stats['auto_dropped']
            self.stats['ai_dropped'] = self.classifier.stats['drop']
            self.stats['ai_kept'] = self.classifier.stats['keep']
            self.stats['ai_error'] = self.classifier.stats['error']

            # 从 MD 中移除 DROP 图片
            if drop_urls:
                content = remove_images_from_content(content, drop_urls)
                print(f"    🗑️ 已移除 {len(drop_urls)} 张装饰性图片")

            if not keep_urls:
                # 所有图片都被 DROP 了，直接写回
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"    ✅ 无需上传（所有图片均为装饰图）")
                return True

            print(f"    📤 准备上传 {len(keep_urls)} 张内容图片")

        # ---- Step 3: 检查缓存 ----
        upload_urls = []
        for url in keep_urls:
            if url in self.global_url_map:
                self.stats['skipped'] += 1
            else:
                upload_urls.append(url)

        if not upload_urls:
            # 全部缓存命中，只需替换
            new_content = content
            for old, new in self.global_url_map.items():
                new_content = new_content.replace(old, new)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"    ⚡ 所有图片缓存命中")
            return True

        # ---- Step 4: 下载 + 上传 ----
        print(f"    ⬆️ 上传中（{len(upload_urls)} 张）...")

        for i, url in enumerate(upload_urls):
            self.stats['total_images'] += 1
            print(f"       [{i+1}/{len(upload_urls)}] ", end="", flush=True)

            # 下载（如果分类阶段已下载过，复用）
            img_data = None
            if self.classifier and url in classified_downloads:
                img_data = classified_downloads[url]
                print(f"📦 复用...", end="", flush=True)
            else:
                img_data = self._download_image(url)

            if not img_data:
                print(f"❌ 下载失败")
                self.stats['failed'] += 1
                self.failed_records.append((file_path, url, "下载失败"))
                continue

            # WebP 转换
            content_type = "image/jpeg"
            fname = f"img_{int(time.time() * 1000) + i}.jpg"

            if PIL_AVAILABLE:
                webp_data = self.convert_to_webp(img_data)
                if webp_data:
                    img_data = webp_data
                    fname = f"img_{int(time.time() * 1000) + i}.webp"
                    content_type = "image/webp"
                    self.stats['converted'] += 1

            # 上传
            new_url = self.upload_to_host(img_data, fname, content_type)
            if new_url:
                print(f"✅")
                self.global_url_map[url] = new_url
                self._save_cache()
                self.stats['success'] += 1
            else:
                print(f"❌ 上传失败")
                self.stats['failed'] += 1
                self.failed_records.append((file_path, url, "上传失败"))

        # ---- Step 5: 替换 URL 并写回文件 ----
        new_content = content
        for old, new in self.global_url_map.items():
            new_content = new_content.replace(old, new)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"    ✅ 文章处理完成")
        return True

    def process_files(self, file_paths: List[str]) -> Dict:
        """处理多篇文章（逐篇串行：分类 → 上传 → 完成）"""
        print(f"\n🖼️ 开始图片处理")
        print(f"   图床地址: {self.host_url}")
        print(f"   智能分类: {'✅ 已启用' if self.classifier else '⏭️ 未启用'}")
        print(f"   文件数量: {len(file_paths)}")
        print()

        for file_path in file_paths:
            if os.path.exists(file_path):
                print(f"📄 {os.path.basename(file_path)}")
                self.process_file(file_path)
                print()

        # 打印统计
        print("=" * 45)
        print(f"📊 处理统计")
        print(f"  📄 处理文件: {self.stats['total_files']} 个")
        print(f"  ✅ 成功上传: {self.stats['success']}")
        print(f"  🖼️ WebP 转换: {self.stats['converted']}")
        print(f"  ⏩ 缓存命中: {self.stats['skipped']}")
        print(f"  ❌ 失败数量: {self.stats['failed']}")

        if self.classifier:
            total_dropped = self.stats['auto_dropped'] + self.stats['ai_dropped']
            print(f"  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─")
            print(f"  🧠 图片分类:")
            print(f"     .gif/.svg 预删: {self.stats['auto_dropped']}")
            print(f"     🗑️ AI 判定删除: {self.stats['ai_dropped']}")
            print(f"     ✅ AI 判定保留: {self.stats['ai_kept']}")
            print(f"     ⚠️ AI 分类失败: {self.stats['ai_error']}（默认保留）")
            if self.stats['auto_dropped'] + self.stats['ai_dropped'] + self.stats['ai_kept'] > 0:
                total_classified = (self.stats['auto_dropped'] +
                                    self.stats['ai_dropped'] +
                                    self.stats['ai_kept'])
                print(f"     📊 节省上传: {total_dropped}/{total_classified} "
                      f"张（{(total_dropped/max(total_classified,1)*100):.0f}%）")
        print("=" * 45)

        return self.stats


def main():
    # 获取待处理的文件列表
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        processed_dir = Path("processed")
        if processed_dir.exists():
            files = list(processed_dir.glob("*.md"))
            files = [str(f) for f in files]
        else:
            print("❌ 未找到 processed 目录")
            set_github_output('success', 'false')
            return

    if not files:
        print("❌ 没有待处理的文件")
        set_github_output('success', 'false')
        return

    # 检查环境变量
    api_token = os.getenv('IMAGE_API_TOKEN')
    if not api_token:
        print("❌ 未设置 IMAGE_API_TOKEN")
        set_github_output('success', 'false')
        return

    # 检查是否跳过分类
    skip_clf = os.getenv('SKIP_IMAGE_CLASSIFICATION', 'false').lower() == 'true'
    enable_clf = not skip_clf

    # 创建迁移器并处理
    migrator = WebPMigrator(enable_classification=enable_clf)
    stats = migrator.process_files(files)

    # 保存失败记录
    if migrator.failed_records:
        with open('image_migration_failed.json', 'w', encoding='utf-8') as f:
            json.dump(migrator.failed_records, f, ensure_ascii=False, indent=2)
        print(f"\n⚠️ 失败记录已保存到 image_migration_failed.json")

    # 输出 GitHub Actions 变量
    success = stats['failed'] == 0 or stats['success'] > 0
    set_github_output('success', str(success).lower())

    # 生成统计信息
    lines = [
        f"- 📄 处理文件: {stats['total_files']} 个",
        f"- ✅ 成功上传: {stats['success']}",
        f"- 🖼️ WebP 转换: {stats['converted']}",
        f"- ⏩ 缓存命中: {stats['skipped']}",
        f"- ❌ 失败数量: {stats['failed']}",
    ]
    if stats['classification_enabled']:
        total_dropped = stats['auto_dropped'] + stats['ai_dropped']
        total_classified = (stats['auto_dropped'] + stats['ai_dropped'] + stats['ai_kept'])
        lines.append(f"- 🧠 图片分类: {stats['ai_kept']} 保留, {total_dropped} 删除 "
                     f"({total_dropped}/{max(total_classified,1)} 张节省)")

    set_github_output('stats', '\n'.join(lines))


if __name__ == "__main__":
    main()
