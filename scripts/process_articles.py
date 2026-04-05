#!/usr/bin/env python3
"""
AI 文章处理脚本 - 使用 AI 将微信公众号文章转换为 Hexo 格式
支持 OpenAI 和 Gemini API（新版 google-genai SDK）

健壮性设计：
- 多 API Key 轮换：支持配置多个 Gemini/OpenAI Key，自动切换
- 自动重试：遇到 503/429 等瞬时错误，指数退避重试
- 自动降级：主提供商失败时，自动切换到备用提供商
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# 添加脚本目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_manager import StatusManager


# ==================== 常量配置 ====================
MAX_RETRIES = 3           # 每个 Key 最大重试次数
RETRY_BASE_DELAY = 5      # 重试基础延迟（秒）
RETRY_MAX_DELAY = 60      # 重试最大延迟（秒）
RETRYABLE_CODES = {503, 429, 500, 502}  # 可重试的 HTTP 状态码


def set_github_output(name: str, value: str):
    """设置 GitHub Actions 输出（使用新的环境文件方式）"""
    github_output = os.getenv('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as f:
            if '\n' in value:
                f.write(f"{name}<<EOF\n{value}\nEOF\n")
            else:
                f.write(f"{name}={value}\n")
    print(f"📋 {name}: {value[:100]}{'...' if len(value) > 100 else ''}")


def parse_api_keys(key_str: str) -> List[str]:
    """解析逗号分隔的 API Key 字符串，返回非空 Key 列表"""
    if not key_str:
        return []
    return [k.strip() for k in key_str.split(',') if k.strip()]


def is_retryable_error(error: Exception) -> bool:
    """判断错误是否可重试（503/429 等瞬时错误）"""
    error_str = str(error).lower()
    # 检查 HTTP 状态码
    for code in RETRYABLE_CODES:
        if str(code) in error_str:
            return True
    # 检查常见错误关键词
    retryable_keywords = ['unavailable', 'rate limit', 'quota', 'overloaded',
                          'timeout', 'connection', 'temporarily']
    return any(kw in error_str for kw in retryable_keywords)


class AIProvider:
    """AI 服务提供商基类"""

    def __init__(self):
        self.model = None
        self.provider_name = "unknown"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送聊天请求"""
        raise NotImplementedError

    def get_model_name(self) -> str:
        """获取当前使用的模型名称"""
        return self.model


class GeminiProvider(AIProvider):
    """Google Gemini API 提供商（使用新版 google-genai SDK）
    
    支持多个 API Key：传入 api_keys 列表，失败时自动轮换。
    """

    def __init__(self, api_keys: List[str] = None, model: str = None):
        from google import genai

        self.provider_name = "gemini"
        self.model = model or os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')

        if not api_keys:
            api_keys = parse_api_keys(os.getenv('GEMINI_API_KEY', ''))

        if not api_keys:
            raise ValueError("GEMINI_API_KEY 环境变量未设置")

        self.api_keys = api_keys
        self.current_key_index = 0

        # 使用第一个 Key 初始化客户端
        self.client = genai.Client(api_key=self.api_keys[0])
        print(f"  🔑 Gemini 已加载 {len(self.api_keys)} 个 API Key")

    def _switch_to_next_key(self):
        """切换到下一个 API Key"""
        old_index = self.current_key_index
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        if self.current_key_index == 0 and len(self.api_keys) == 1:
            print(f"    ⚠️ 仅有一个 Gemini Key，无法切换")
            return False

        from google import genai
        self.client = genai.Client(api_key=self.api_keys[self.current_key_index])
        # 隐藏 key 中间部分
        key = self.api_keys[self.current_key_index]
        masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        print(f"    🔄 切换到 Gemini Key #{self.current_key_index + 1} ({masked})")
        return True

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送 Gemini 聊天请求"""
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config={
                    'system_instruction': system_prompt,
                    'temperature': 0.3,
                    'max_output_tokens': 8192,
                }
            )
            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini API 调用失败: {str(e)}")


class OpenAIProvider(AIProvider):
    """OpenAI API 提供商
    
    支持多个 API Key：传入 api_keys 列表，失败时自动轮换。
    兼容所有 OpenAI 兼容 API（包括各种中转站）。
    """

    def __init__(self, api_keys: List[str] = None, model: str = None,
                 base_url: str = None):
        from openai import OpenAI

        self.provider_name = "openai"
        self.model = model or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        self.base_url = base_url or os.getenv('OPENAI_BASE_URL', '')

        if not api_keys:
            api_keys = parse_api_keys(os.getenv('OPENAI_API_KEY', ''))

        if not api_keys:
            raise ValueError("OPENAI_API_KEY 环境变量未设置")

        self.api_keys = api_keys
        self.current_key_index = 0

        # 初始化客户端
        client_kwargs = {'api_key': self.api_keys[0]}
        if self.base_url:
            client_kwargs['base_url'] = self.base_url
        self.client = OpenAI(**client_kwargs)
        print(f"  🔑 OpenAI 已加载 {len(self.api_keys)} 个 API Key")

    def _switch_to_next_key(self):
        """切换到下一个 API Key"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        if self.current_key_index == 0 and len(self.api_keys) == 1:
            print(f"    ⚠️ 仅有一个 OpenAI Key，无法切换")
            return False

        from openai import OpenAI
        client_kwargs = {'api_key': self.api_keys[self.current_key_index]}
        if self.base_url:
            client_kwargs['base_url'] = self.base_url
        self.client = OpenAI(**client_kwargs)
        key = self.api_keys[self.current_key_index]
        masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        print(f"    🔄 切换到 OpenAI Key #{self.current_key_index + 1} ({masked})")
        return True

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送 OpenAI 聊天请求"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=8192
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"OpenAI API 调用失败: {str(e)}")


class RobustArticleProcessor:
    """带自动重试和多提供商降级的 AI 文章处理器"""

    def __init__(self, primary_provider: str = 'gemini'):
        """初始化处理器

        Args:
            primary_provider: 主 AI 服务提供商 ('gemini' 或 'openai')
        """
        self.primary_provider_name = primary_provider
        self.prompt_template = self._load_prompt_template()
        self.system_prompt = self._extract_system_prompt()

        # 初始化提供商链：主提供商 + 降级提供商
        self.providers: List[AIProvider] = []
        self._init_provider_chain(primary_provider)

    def _init_provider_chain(self, primary: str):
        """初始化提供商链，主提供商在前，备用在后"""
        fallback = 'openai' if primary == 'gemini' else 'gemini'

        # 尝试初始化主提供商
        try:
            if primary == 'gemini':
                provider = GeminiProvider()
            else:
                provider = OpenAIProvider()
            self.providers.append(provider)
            print(f"📦 主提供商: {primary.upper()} ({provider.get_model_name()})")
        except Exception as e:
            print(f"  ⚠️ 主提供商 {primary.upper()} 初始化失败: {e}")

        # 尝试初始化降级提供商
        if fallback != primary:
            try:
                if fallback == 'gemini':
                    provider = GeminiProvider()
                else:
                    provider = OpenAIProvider()
                self.providers.append(provider)
                print(f"📦 备用提供商: {fallback.upper()} ({provider.get_model_name()})")
            except Exception as e:
                print(f"  ⚠️ 备用提供商 {fallback.upper()} 初始化失败: {e}")

        if not self.providers:
            raise RuntimeError("所有 AI 提供商初始化失败，无法继续处理")

    def _load_prompt_template(self) -> str:
        """加载提示词模板"""
        prompt_path = Path("prompts/transform.txt")
        if prompt_path.exists():
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        raise FileNotFoundError("未找到提示词模板文件 prompts/transform.txt")

    def _extract_system_prompt(self) -> str:
        """从模板中提取系统提示词（第一段，--- 之前的部分）"""
        if '---' in self.prompt_template:
            return self.prompt_template.split('---')[0].strip()
        return "你是一个专业的内容编辑。"

    def _get_user_prompt(self, content: str, has_pre_classification: bool = False) -> str:
        """构建用户提示词

        将模板中系统提示词（第一个 --- 之前）之后的所有内容作为用户提示词，
        并将 {content} 占位符替换为实际文章内容。

        Args:
            content: 文章内容
            has_pre_classification: 是否已完成图片预分类
                若为 True，会在提示词中注入提示，告知 AI 装饰图已被移除
        """
        if '{content}' in self.prompt_template:
            first_separator_idx = self.prompt_template.index('---')
            user_template = self.prompt_template[first_separator_idx + 3:].strip()
            result = user_template.replace('{content}', content)
        else:
            result = self.prompt_template.replace('{content}', content)

        # 图片预分类完成后，注入提示告知 AI 装饰图已被移除
        if has_pre_classification:
            classification_note = (
                "\n> **⚠️ 重要提示**：本文中的装饰性图片（分割线、二维码、"
                "纯装饰元素等）已通过视觉AI预分类自动移除。"
                "**请保留文中所有剩余图片**，不要删除任何图片，"
                "只需为每张图片添加描述性 alt 文本即可。\n\n"
            )
            result = classification_note + result

        return result

    def read_article(self, file_path: str) -> str:
        """读取原始文章"""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def extract_title(self, content: str) -> str:
        """从文章内容中提取标题"""
        lines = content.strip().split('\n')
        for line in lines[:10]:
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
            if line and len(line) < 100:
                title = re.sub(r'^[【\[](.+?)[】\]]', r'\1', line)
                if title and not title.startswith('!['):
                    return title
        return "未命名文章"

    def generate_filename(self, title: str, date_str: str = None) -> str:
        """生成文件名"""
        filename = title
        filename = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', filename)
        filename = re.sub(r'[\s]+', '-', filename)
        filename = filename[:50]

        if date_str:
            try:
                date_prefix = datetime.strptime(date_str[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
            except:
                date_prefix = datetime.now().strftime('%Y-%m-%d')
        else:
            date_prefix = datetime.now().strftime('%Y-%m-%d')

        return f"{date_prefix}-{filename}.md"

    def _clean_ai_output(self, content: str) -> str:
        """清理 AI 输出，去除可能包裹的 markdown 代码块"""
        content = content.strip()

        code_block_pattern = r'^```(?:markdown|md)?\s*\n(.*?)\n```\s*$'
        match = re.match(code_block_pattern, content, re.DOTALL)
        if match:
            content = match.group(1).strip()
            print(f"  🧹 已去除 AI 输出的 markdown 代码块包裹")

        lines = content.split('\n')
        while lines and lines[0].strip().startswith('<!--'):
            lines.pop(0)
        content = '\n'.join(lines).strip()

        return content

    def extract_date_from_processed(self, content: str) -> Optional[str]:
        """从处理后的文章中提取日期"""
        match = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2})', content, re.MULTILINE)
        if match:
            return match.group(1)
        return None

    def extract_title_from_processed(self, content: str) -> Optional[str]:
        """从处理后的文章中提取标题"""
        match = re.search(r'^title:\s*\[?(.+?)\]?\s*$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None

    def _call_with_retry(self, provider: AIProvider, system_prompt: str,
                         user_prompt: str) -> str:
        """对单个提供商进行带重试的调用
        
        策略：
        1. 遇到可重试错误 → 指数退避重试当前 Key
        2. 重试耗尽 → 切换下一个 Key 继续重试
        3. 所有 Key 耗尽 → 抛出异常
        """
        provider_name = provider.provider_name.upper()
        max_attempts = MAX_RETRIES * len(provider.api_keys)

        for attempt in range(max_attempts):
            try:
                return provider.chat(system_prompt, user_prompt)

            except Exception as e:
                error_str = str(e)
                is_retryable = is_retryable_error(e)

                if not is_retryable or attempt == max_attempts - 1:
                    # 不可重试错误或已耗尽所有尝试
                    raise

                # 计算退避延迟
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt // len(provider.api_keys))),
                            RETRY_MAX_DELAY)

                print(f"    ⏳ {provider_name} 请求失败 ({error_str[:80]}...)，"
                      f"{delay}秒后{'切换Key并' if (attempt + 1) % MAX_RETRIES == 0 else ''}重试"
                      f" [{attempt + 1}/{max_attempts}]")

                time.sleep(delay)

                # 每个Key重试 MAX_RETRIES 次后切换 Key
                if (attempt + 1) % MAX_RETRIES == 0:
                    provider._switch_to_next_key()

        raise RuntimeError(f"{provider_name} 所有 Key 均已耗尽重试次数")

    def process_with_ai(self, content: str, has_pre_classification: bool = False) -> str:
        """使用 AI 处理文章（自动重试 + 降级）

        依次尝试提供商链中的每个提供商，
        每个提供商内部进行多 Key 轮换重试。

        Args:
            content: 文章内容
            has_pre_classification: 是否已完成图片预分类
        """
        user_prompt = self._get_user_prompt(content, has_pre_classification)
        errors = []

        for i, provider in enumerate(self.providers):
            provider_name = provider.provider_name.upper()
            is_fallback = i > 0

            if is_fallback:
                print(f"  🔄 主提供商失败，降级到备用提供商 {provider_name}")

            try:
                result = self._call_with_retry(
                    provider, self.system_prompt, user_prompt
                )
                if is_fallback:
                    print(f"  ✅ {provider_name} 降级处理成功")
                return result

            except Exception as e:
                errors.append(f"{provider_name}: {str(e)}")
                if is_fallback:
                    # 备用也失败了，不再尝试
                    break

        # 所有提供商都失败
        error_detail = "; ".join(errors)
        raise RuntimeError(f"所有 AI 提供商均失败 - {error_detail}")

    def process_article(self, article: Dict[str, Any],
                       output_dir: str = "processed",
                       classification_results: Dict = None) -> Dict[str, Any]:
        """处理单篇文章

        Args:
            article: 文章信息字典
            output_dir: 输出目录
            classification_results: 图片预分类结果
                {article_id: {drop_urls: [...], keep_urls: [...]}}
        """
        result = {
            "article_id": article["id"],
            "success": False,
            "output_path": None,
            "title": None,
            "error": None
        }

        try:
            raw_content = self.read_article(article["source"])
            original_title = self.extract_title(raw_content)

            print(f"  📖 正在处理: {original_title}")

            # ---- 图片预分类处理 ----
            # 在发送给文本 AI 前，移除已被视觉模型识别为装饰性的图片
            has_pre_classification = False
            if classification_results and article["id"] in classification_results:
                article_clf = classification_results[article["id"]]
                drop_urls = set(article_clf.get("drop_urls", []))
                if drop_urls:
                    from image_classifier import remove_images_from_content
                    raw_content = remove_images_from_content(raw_content, drop_urls)
                    kept = article_clf.get("keep_urls", [])
                    print(f"  🖼️ 预分类: 已移除 {len(drop_urls)} 张装饰图，"
                          f"保留 {len(kept)} 张内容图")
                    has_pre_classification = True
                else:
                    print(f"  🖼️ 预分类: 无需移除装饰图")
                    has_pre_classification = True

            # AI 处理（自动重试 + 降级）
            processed_content = self.process_with_ai(raw_content, has_pre_classification)

            # 清理 AI 输出
            processed_content = self._clean_ai_output(processed_content)

            # 提取日期和标题
            extracted_date = self.extract_date_from_processed(processed_content)
            extracted_title = self.extract_title_from_processed(processed_content) or original_title

            # 生成文件名并保存
            filename = self.generate_filename(extracted_title, extracted_date)
            output_path = Path(output_dir) / filename
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(processed_content)

            result["success"] = True
            result["output_path"] = str(output_path)
            result["title"] = extracted_title
            print(f"  ✅ 处理完成: {filename}")

        except Exception as e:
            result["error"] = str(e)
            print(f"  ❌ 处理失败: {str(e)}")

        return result


def main():
    """主函数"""
    ai_provider = os.getenv('AI_PROVIDER', 'gemini').lower()

    print(f"\n🤖 使用 AI 提供商: {ai_provider.upper()}")

    manager = StatusManager()

    # 读取待处理文章列表
    pending_path = Path("pending_articles.json")
    if not pending_path.exists():
        set_github_output('success', 'false')
        print("❌ 未找到待处理文章列表")
        return

    with open(pending_path, 'r', encoding='utf-8') as f:
        pending_articles = json.load(f)

    if not pending_articles:
        set_github_output('success', 'false')
        print("❌ 没有待处理的文章")
        return

    print(f"\n🤖 开始 AI 处理，共 {len(pending_articles)} 篇文章\n")

    # 加载图片预分类结果（由 pre_classify_images.py 生成）
    classification_results = {}
    clf_path = Path("image_classification_results.json")
    if clf_path.exists():
        try:
            with open(clf_path, 'r', encoding='utf-8') as f:
                classification_results = json.load(f)
            if classification_results:
                total_clf = len(classification_results)
                print(f"🖼️ 已加载图片预分类结果（{total_clf} 篇文章）")
        except Exception as e:
            print(f"⚠️ 图片预分类结果加载失败: {e}，将跳过预分类")
    else:
        print("ℹ️ 未找到图片预分类结果，文本AI将自行处理图片")

    # 初始化处理器（自动构建主+备用提供商链）
    try:
        processor = RobustArticleProcessor(primary_provider=ai_provider)
    except Exception as e:
        print(f"❌ AI 处理器初始化失败: {str(e)}")
        set_github_output('success', 'false')
        return

    print()

    results = []
    article_list_md = []

    for article in pending_articles:
        # 标记为处理中
        manager.mark_processing(article["id"])

        # 处理文章（传入图片预分类结果）
        result = processor.process_article(
            article, classification_results=classification_results)
        results.append(result)

        # 更新状态
        if result["success"]:
            manager.mark_completed(article["id"], result["output_path"], result.get("title"))
            article_list_md.append(f"- ✅ {result.get('title', article['id'])}")
        else:
            manager.mark_failed(article["id"], result["error"] or "未知错误")
            article_list_md.append(f"- ❌ {article['id']} - {result['error']}")

    # 统计结果
    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    print(f"\n📊 处理结果:")
    print(f"  - 成功: {success_count}")
    print(f"  - 失败: {fail_count}")

    set_github_output('success', 'true' if success_count > 0 else 'false')
    set_github_output('success_count', str(success_count))

    article_list = '\n'.join(article_list_md)
    with open('article_list.md', 'w', encoding='utf-8') as f:
        f.write(article_list)
    set_github_output('article_list', article_list)


if __name__ == "__main__":
    main()
