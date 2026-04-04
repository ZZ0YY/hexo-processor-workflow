#!/usr/bin/env python3
"""
GitHub Actions 工具函数
"""

import os


def set_github_output(name: str, value: str):
    """设置 GitHub Actions 输出（使用新的环境文件方式）
    
    Args:
        name: 输出变量名
        value: 输出值
    """
    github_output = os.getenv('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as f:
            # 多行值需要特殊处理
            if '\n' in value:
                # 使用 heredoc 语法
                f.write(f"{name}<<EOF\n{value}\nEOF\n")
            else:
                f.write(f"{name}={value}\n")
    # 同时打印到控制台（用于本地调试）
    print(f"📋 {name}={value}")
