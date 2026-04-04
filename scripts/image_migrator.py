#!/usr/bin/env python3
"""
微信图片迁移脚本 - 将微信图片转换为 WebP 并上传到图床
在 AI 处理完成后运行，节省图床空间
"""

import re
import os
import requests
import time
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# 尝试导入 PIL，如果失败则不进行格式转换
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ Pillow 未安装，将跳过 WebP 转换")

# 添加脚本目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from github_utils import set_github_output


class WebPMigrator:
    """微信图片迁移器"""
    
    def __init__(self, config: Dict = None):
        # 从环境变量或配置获取参数
        self.host_url = os.getenv('IMAGE_HOST_URL', config.get('host_url', 'https://photo.20080601.xyz') if config else 'https://photo.20080601.xyz')
        self.display_url = os.getenv('IMAGE_DISPLAY_URL', config.get('display_url', 'https://photo1.20080601.xyz') if config else 'https://photo1.20080601.xyz')
        self.api_token = os.getenv('IMAGE_API_TOKEN', config.get('api_token', '') if config else '')
        self.upload_channel = os.getenv('IMAGE_UPLOAD_CHANNEL', 'telegram')
        self.target_folder = os.getenv('IMAGE_TARGET_FOLDER', 'wx')
        
        self.upload_api_url = f"{self.host_url}/upload"
        self.cache_file = "image_migration_cache.json"
        
        # 统计数据
        self.stats = {
            'total_files': 0,
            'total_images': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'converted': 0
        }
        self.failed_records = []
        self.global_url_map = self._load_cache()
    
    def _load_cache(self) -> Dict:
        """加载 URL 映射缓存"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_cache(self):
        """保存 URL 映射缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.global_url_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"    [!] 缓存保存失败: {e}")
    
    def _request_with_retry(self, method: str, url: str, **kwargs):
        """带重试的请求"""
        for attempt in range(3):
            try:
                if method == 'GET':
                    response = requests.get(url, **kwargs)
                else:
                    response = requests.post(url, **kwargs)
                response.raise_for_status()
                return response
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise e
    
    def convert_to_webp(self, image_data: bytes) -> Optional[bytes]:
        """将图片转换为 WebP 格式"""
        if not PIL_AVAILABLE:
            return None
        
        try:
            img = Image.open(BytesIO(image_data))
            if img.mode == 'CMYK':
                img = img.convert('RGB')
            output_buffer = BytesIO()
            img.save(output_buffer, format='WEBP', quality=80, method=4)
            return output_buffer.getvalue()
        except Exception as e:
            print(f"    ⚠️ 图片转换失败 (将使用原图): {e}")
            return None
    
    def download_image(self, url: str) -> Tuple[Optional[bytes], Optional[str], str]:
        """下载图片"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://mp.weixin.qq.com/'
            }
            response = self._request_with_retry('GET', url, headers=headers, timeout=15)
            filename = f"img_{int(time.time() * 1000)}.jpg"
            return response.content, filename, response.headers.get('Content-Type', 'image/jpeg')
        except Exception as e:
            return None, None, str(e)
    
    def upload_to_host(self, image_data: bytes, filename: str, content_type: str) -> Optional[str]:
        """上传图片到图床"""
        try:
            headers = {"Authorization": f"Bearer {self.api_token}"}
            params = {
                'uploadChannel': self.upload_channel,
                'uploadFolder': self.target_folder
            }
            files = {'file': (filename, image_data, content_type)}
            
            response = self._request_with_retry(
                'POST', 
                self.upload_api_url, 
                files=files, 
                headers=headers, 
                params=params, 
                timeout=30
            )
            result = response.json()
            
            if isinstance(result, list) and len(result) > 0 and 'src' in result[0]:
                relative_path = result[0]['src']
                if relative_path.startswith('http'):
                    final_url = relative_path
                else:
                    final_url = f"{self.host_url}{relative_path}"
                
                # 域名替换
                final_url = final_url.replace(self.host_url, self.display_url)
                return final_url
            return None
        except Exception as e:
            print(f"    上传失败: {e}")
            return None
    
    def process_file(self, file_path: str, output_dir: str = None) -> bool:
        """处理单个文件中的图片
        
        Args:
            file_path: 输入文件路径
            output_dir: 输出目录（如果为None则覆盖原文件）
        
        Returns:
            是否成功处理
        """
        self.stats['total_files'] += 1
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"  ❌ 无法读取文件: {e}")
            return False
        
        # 匹配图片语法 ![alt](url)
        matches = re.findall(r'!\[(.*?)\]\((http.*?)\)', content)
        unique_urls = list(set([url for _, url in matches]))
        
        # 排除已经处理过的图片
        pending_urls = [
            u for u in unique_urls 
            if self.host_url not in u and self.display_url not in u
        ]
        
        if not pending_urls:
            return True  # 无需处理
        
        print(f"  📷 发现 {len(pending_urls)} 张新图片")
        
        for i, url in enumerate(pending_urls):
            self.stats['total_images'] += 1
            
            # 检查缓存
            if url in self.global_url_map:
                print(f"     [{i+1}/{len(pending_urls)}] ⚡ 缓存命中")
                self.stats['skipped'] += 1
                continue
            
            print(f"     [{i+1}/{len(pending_urls)}] 🔽 下载...", end="", flush=True)
            
            # 下载图片
            img_data, fname, err_or_type = self.download_image(url)
            
            if not img_data:
                print(f" ❌ 下载失败: {err_or_type}")
                self.stats['failed'] += 1
                self.failed_records.append((file_path, url, f"下载失败: {err_or_type}"))
                continue
            
            # 转换为 WebP
            if PIL_AVAILABLE:
                print(f" 📸 压缩...", end="", flush=True)
                webp_data = self.convert_to_webp(img_data)
                if webp_data:
                    img_data = webp_data
                    fname = os.path.splitext(fname)[0] + ".webp"
                    err_or_type = "image/webp"
                    self.stats['converted'] += 1
            
            # 上传
            print(f" ⬆️ 上传...", end="", flush=True)
            new_url = self.upload_to_host(img_data, fname, err_or_type)
            
            if new_url:
                print(f" ✅")
                self.global_url_map[url] = new_url
                self._save_cache()
                self.stats['success'] += 1
            else:
                print(f" ❌ 上传失败")
                self.stats['failed'] += 1
                self.failed_records.append((file_path, url, "上传失败"))
        
        # 替换文件中的链接
        new_content = content
        for old, new in self.global_url_map.items():
            new_content = new_content.replace(old, new)
        
        # 写入文件
        output_path = file_path if output_dir is None else os.path.join(output_dir, os.path.basename(file_path))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return True
    
    def process_files(self, file_paths: List[str]) -> Dict:
        """处理多个文件
        
        Args:
            file_paths: 文件路径列表
        
        Returns:
            处理统计信息
        """
        print(f"\n🖼️ 开始图片迁移处理")
        print(f"   图床地址: {self.host_url}")
        print(f"   显示域名: {self.display_url}")
        print(f"   文件数量: {len(file_paths)}")
        print()
        
        for file_path in file_paths:
            if os.path.exists(file_path):
                print(f"📄 {os.path.basename(file_path)}")
                self.process_file(file_path)
        
        # 打印统计
        print("\n" + "="*40)
        print(f"📊 图片迁移统计")
        print(f"📄 处理文件: {self.stats['total_files']} 个")
        print(f"✅ 成功上传: {self.stats['success']}")
        print(f"🖼️ 格式转换: {self.stats['converted']} (WebP)")
        print(f"⏩ 缓存命中: {self.stats['skipped']}")
        print(f"❌ 失败数量: {self.stats['failed']}")
        print("="*40)
        
        return self.stats


def main():
    """主函数 - 处理 processed 目录下的所有文件"""
    
    # 获取待处理的文件列表
    if len(sys.argv) > 1:
        # 从命令行参数获取文件列表
        files = sys.argv[1:]
    else:
        # 默认处理 processed 目录
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
    
    # 检查必要的环境变量
    api_token = os.getenv('IMAGE_API_TOKEN')
    if not api_token:
        print("❌ 未设置 IMAGE_API_TOKEN 环境变量")
        set_github_output('success', 'false')
        return
    
    # 创建迁移器并处理
    migrator = WebPMigrator()
    stats = migrator.process_files(files)
    
    # 保存失败记录
    if migrator.failed_records:
        with open('image_migration_failed.json', 'w', encoding='utf-8') as f:
            json.dump(migrator.failed_records, f, ensure_ascii=False, indent=2)
        print(f"\n⚠️ 失败记录已保存到 image_migration_failed.json")
    
    # 输出 GitHub Actions 变量
    success = stats['failed'] == 0 or stats['success'] > 0
    set_github_output('success', str(success).lower())
    
    # 生成统计信息（用于 PR 描述）
    stats_md = f"""- 📄 处理文件: {stats['total_files']} 个
- ✅ 成功上传: {stats['success']}
- 🖼️ WebP 转换: {stats['converted']}
- ⏩ 缓存命中: {stats['skipped']}
- ❌ 失败数量: {stats['failed']}"""
    set_github_output('stats', stats_md)


if __name__ == "__main__":
    main()
