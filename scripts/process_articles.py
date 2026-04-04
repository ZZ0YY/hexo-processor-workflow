#!/usr/bin/env python3
"""
AI 文章处理脚本 - 使用 AI 将微信公众号文章转换为 Hexo 格式
支持 OpenAI 和 Gemini API
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# 添加脚本目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_manager import StatusManager


class AIProvider:
    """AI 服务提供商基类"""
    
    def __init__(self):
        self.model = None
        self.prompt_template = None
    
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送聊天请求"""
        raise NotImplementedError
    
    def get_model_name(self) -> str:
        """获取当前使用的模型名称"""
        return self.model


class OpenAIProvider(AIProvider):
    """OpenAI API 提供商"""
    
    def __init__(self):
        from openai import OpenAI
        
        self.client = OpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            base_url=os.getenv('OPENAI_BASE_URL')  # 可选：自定义 API 端点
        )
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    
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


class GeminiProvider(AIProvider):
    """Google Gemini API 提供商"""
    
    def __init__(self):
        import google.generativeai as genai
        
        self.api_key = os.getenv('GEMINI_API_KEY')
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY 环境变量未设置")
        
        genai.configure(api_key=self.api_key)
        self.genai = genai
        self.model = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
        
        # 配置生成参数
        self.generation_config = genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=8192,
        )
    
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送 Gemini 聊天请求"""
        try:
            model = self.genai.GenerativeModel(
                model_name=self.model,
                generation_config=self.generation_config,
                system_instruction=system_prompt
            )
            
            response = model.generate_content(user_prompt)
            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini API 调用失败: {str(e)}")


class ArticleProcessor:
    """AI 文章处理器"""
    
    def __init__(self, provider: str = 'gemini'):
        """初始化处理器
        
        Args:
            provider: AI 服务提供商，支持 'gemini' 或 'openai'
        """
        self.provider = self._init_provider(provider)
        self.prompt_template = self._load_prompt_template()
        self.system_prompt = self._extract_system_prompt()
    
    def _init_provider(self, provider: str) -> AIProvider:
        """初始化 AI 提供商"""
        providers = {
            'gemini': GeminiProvider,
            'openai': OpenAIProvider
        }
        
        if provider not in providers:
            raise ValueError(f"不支持的 AI 提供商: {provider}，支持: {list(providers.keys())}")
        
        return providers[provider]()
    
    def _load_prompt_template(self) -> str:
        """加载提示词模板"""
        prompt_path = Path("prompts/transform.txt")
        if prompt_path.exists():
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        
        raise FileNotFoundError("未找到提示词模板文件 prompts/transform.txt")
    
    def _extract_system_prompt(self) -> str:
        """从模板中提取系统提示词（--- 之前的部分）"""
        if '---' in self.prompt_template:
            return self.prompt_template.split('---')[0].strip()
        return "你是一个专业的内容编辑。"
    
    def _get_user_prompt(self, content: str) -> str:
        """构建用户提示词"""
        # 查找模板中的占位符部分
        if '{content}' in self.prompt_template:
            # 使用 --- 分隔后的部分作为用户提示词模板
            parts = self.prompt_template.split('---')
            if len(parts) > 1:
                user_template = parts[1].strip()
                return user_template.replace('{content}', content)
        
        # 直接使用整个模板
        return self.prompt_template.replace('{content}', content)
    
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
        """生成文件名
        
        Args:
            title: 文章标题
            date_str: 日期字符串（从文章中提取），格式 YYYY-MM-DD
        """
        # 简化标题用于文件名
        filename = title
        # 移除特殊字符
        filename = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', filename)
        filename = re.sub(r'[\s]+', '-', filename)
        filename = filename[:50]  # 限制长度
        
        # 使用文章日期或当前日期
        if date_str:
            try:
                date_prefix = datetime.strptime(date_str[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
            except:
                date_prefix = datetime.now().strftime('%Y-%m-%d')
        else:
            date_prefix = datetime.now().strftime('%Y-%m-%d')
        
        return f"{date_prefix}-{filename}.md"
    
    def process_with_ai(self, content: str) -> str:
        """使用 AI 处理文章"""
        user_prompt = self._get_user_prompt(content)
        
        return self.provider.chat(self.system_prompt, user_prompt)
    
    def extract_date_from_processed(self, content: str) -> Optional[str]:
        """从处理后的文章中提取日期"""
        # 从 front matter 中提取日期
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
    
    def process_article(self, article: Dict[str, Any], output_dir: str = "processed") -> Dict[str, Any]:
        """处理单篇文章"""
        result = {
            "article_id": article["id"],
            "success": False,
            "output_path": None,
            "title": None,
            "error": None
        }
        
        try:
            # 读取原文
            raw_content = self.read_article(article["source"])
            
            # 提取原标题（用于显示）
            original_title = self.extract_title(raw_content)
            
            print(f"  📖 正在处理: {original_title}")
            
            # AI 处理
            processed_content = self.process_with_ai(raw_content)
            
            # 从处理结果中提取日期和标题
            extracted_date = self.extract_date_from_processed(processed_content)
            extracted_title = self.extract_title_from_processed(processed_content) or original_title
            
            # 生成文件名
            filename = self.generate_filename(extracted_title, extracted_date)
            output_path = Path(output_dir) / filename
            
            # 确保输出目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存处理后的文章
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
    # 获取 AI 提供商配置
    ai_provider = os.getenv('AI_PROVIDER', 'gemini').lower()
    
    print(f"\n🤖 使用 AI 提供商: {ai_provider.upper()}")
    
    manager = StatusManager()
    
    # 读取待处理文章列表
    pending_path = Path("pending_articles.json")
    if not pending_path.exists():
        print("::set-output name=success::false")
        print("❌ 未找到待处理文章列表")
        return
    
    with open(pending_path, 'r', encoding='utf-8') as f:
        pending_articles = json.load(f)
    
    if not pending_articles:
        print("::set-output name=success::false")
        print("❌ 没有待处理的文章")
        return
    
    print(f"\n🤖 开始 AI 处理，共 {len(pending_articles)} 篇文章\n")
    
    try:
        processor = ArticleProcessor(provider=ai_provider)
    except Exception as e:
        print(f"❌ AI 提供商初始化失败: {str(e)}")
        print("::set-output name=success::false")
        return
    
    results = []
    article_list_md = []
    
    for article in pending_articles:
        # 标记为处理中
        manager.mark_processing(article["id"])
        
        # 处理文章
        result = processor.process_article(article)
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
    
    # 设置 GitHub Actions 输出
    print(f"::set-output name=success::{'true' if success_count > 0 else 'false'}")
    print(f"::set-output name=success_count::{success_count}")
    # 多行输出需要特殊处理
    article_list = '\n'.join(article_list_md)
    # 写入文件供后续步骤使用
    with open('article_list.md', 'w', encoding='utf-8') as f:
        f.write(article_list)
    print(f"::set-output name=article_list::{article_list}")


if __name__ == "__main__":
    main()
