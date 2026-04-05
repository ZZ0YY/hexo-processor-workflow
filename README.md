# 惠州仲恺中学 - Hexo 文章自动处理工作流

一个基于 GitHub Actions 的自动化工作流，使用 AI 将微信公众号文章批量转换为规范的 Hexo 博客文章格式，并通过 **跨仓库 Pull Request** 提交供人工审核。

## 特性

- **AI 智能处理** — 支持 Gemini / OpenAI，多 API Key 轮换 + 指数退避重试 + 自动故障切换
- **视觉模型图片分类** — 集成 GLM-4.1V-Thinking-Flash，自动识别并移除装饰性图片（分割线、二维码底图、banner 等）
- **图片自动迁移** — 下载微信图片 → WebP 转换 → 上传图床 → 替换链接，缓存机制避免重复上传
- **跨仓库 PR 审核模式** — 处理仓库与 Hexo 博客仓库分离，自动创建 PR 到目标仓库供人工审核
- **每日限额控制** — 可配置每天处理文章数量，防止 API 超支
- **进度断点续处理** — 完整的状态管理，支持 1000+ 文章的大批量处理
- **质量检查** — 自动验证 Front Matter、分类体系、内容结构
- **智能排序** — 从文件名提取日期，优先处理新文章
- **强制重处理** — 支持对已完成的文章重新处理（如补充封面图上传等）

## 项目结构

```
hexo-processor-workflow/
├── .github/
│   └── workflows/
│       └── hexo-processor-workflow.yml   # GitHub Actions 工作流（15 步完整流程）
├── raw-articles/                          # 待处理的原始文章目录
│   ├── [2026-01-26]文章标题.md
│   └── ...
├── processed/                             # AI 处理后的文章（图片链接已替换）
├── prompts/
│   └── transform.txt                      # AI 提示词模板（可自定义）
├── scripts/
│   ├── requirements.txt                   # Python 依赖
│   ├── status_manager.py                  # 状态管理器（断点续处理）
│   ├── get_pending_articles.py            # 获取待处理文章列表
│   ├── process_articles.py                # AI 处理核心（多 Key 轮换 + 故障切换）
│   ├── image_classifier.py                # 视觉模型图片分类（GLM-4.1V）
│   ├── image_migrator.py                  # 图片迁移（集成智能分类 + WebP 转换）
│   ├── quality_check.py                   # 质量检查
│   ├── check_quota.py                     # 每日配额检查
│   ├── github_utils.py                    # GitHub Actions 工具函数
│   └── generate_report.py                 # 生成处理报告
├── status.json                            # 处理状态追踪文件
└── image_migration_cache.json             # 图片 URL 映射缓存
```

## 工作流程

```
┌─────────────────────────────────────────────┐
│        GitHub Actions 每日/手动触发         │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  检查每日配额 → 获取待处理文章（按日期降序）  │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│          AI 处理文章 (Gemini / OpenAI)        │
│  • 多 Key 轮换 + 指数退避重试                  │
│  • 自动故障切换（Gemini ↔ OpenAI）            │
│  • 生成 Front Matter、分类、标签、摘要        │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│       图片智能分类 + 迁移（逐篇串行）         │
│  1. 提取所有图片 URL（含 cover 字段）         │
│  2. 预过滤 .gif/.svg → 直接删除              │
│  3. 下载图片 → base64 发送给 GLM 视觉模型    │
│  4. AI 判定：KEEP（内容图）/ DROP（装饰图）   │
│  5. 从 Markdown 中移除 DROP 图片            │
│  6. 上传 KEEP 图片到图床（WebP 转换）        │
│  7. 替换文章中的图片链接                     │
│  8. 文章处理完成，进入下一篇                 │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│              质量检查                         │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│   检出目标 Hexo 仓库 → 复制文章 → 创建 PR    │
│   （跨仓库：processor-workflow → hexo-zkzx）  │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│           提交状态更新 + 生成报告              │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│          人工审核 PR 并合并                   │
└─────────────────────────────────────────────┘
```

## 快速开始

### 步骤 1: 准备文章文件

将原始文章放入 `raw-articles/` 目录，支持以下文件名格式：

```
[2026-01-26]聚焦智慧教育赋能精准教学.md    ✅ 推荐
2026-01-26-聚焦智慧教育赋能精准教学.md      ✅ 支持
20260126聚焦智慧教育赋能精准教学.md          ✅ 支持
任意文件名.md                               ⚠️ 会排在最后处理
```

系统会自动从文件名提取日期，**优先处理新文章**。

### 步骤 2: 配置 Secrets

在 GitHub 仓库（`Settings → Secrets and variables → Actions`）中添加以下 Secrets：

| Secret 名称 | 说明 | 必需 |
|------------|------|------|
| `GEMINI_API_KEY` | Google Gemini API 密钥（支持多个 Key，逗号分隔：`key1,key2,key3`） | 二选一 |
| `OPENAI_API_KEY` | OpenAI API 密钥（备用提供商） | 二选一 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址（使用第三方服务时需要） | 否 |
| `HEXO_REPO` | Hexo 目标仓库地址，格式：`username/repo` | ✅ |
| `HEXO_REPO_TOKEN` | 有目标仓库推送权限的 Fine-grained PAT（需要 `Contents: Write` + `Pull requests: Write` 权限） | ✅ |
| `IMAGE_API_TOKEN` | 图床上传 API Token | ✅ |
| `IMAGE_HOST_URL` | 图床上传地址（默认：`https://photo.20080601.xyz`） | 否 |
| `IMAGE_DISPLAY_URL` | 图片显示域名（默认：`https://photo1.20080601.xyz`） | 否 |
| `GLM_API_KEY` | 智谱 GLM API 密钥（用于图片智能分类） | 推荐 |
| `PR_REVIEWERS` | PR 审核者（逗号分隔的 GitHub 用户名） | 否 |

**`HEXO_REPO_TOKEN` 权限说明**：需要创建 Fine-grained Personal Access Token，对目标 Hexo 仓库授权以下权限：
- **Contents**: Read and write
- **Pull requests**: Read and write

### 步骤 3: 自定义提示词（可选）

编辑 `prompts/transform.txt` 文件，根据需求调整 AI 处理提示词。

### 步骤 4: 配置 Variables（可选）

在 `Settings → Secrets and variables → Actions → Variables` 中添加：

| Variable 名称 | 说明 | 默认值 |
|--------------|------|--------|
| `IMAGE_UPLOAD_CHANNEL` | 图床上传通道 | `telegram` |
| `IMAGE_TARGET_FOLDER` | 图床目标文件夹 | `wx` |
| `GLM_MODEL` | GLM 视觉模型名称 | `glm-4.1v-thinking-flash` |

### 步骤 5: 运行工作流

**自动运行**：每天 UTC 00:00（北京时间 08:00）自动执行，默认处理 2 篇。

**手动运行**：
1. 进入 `Actions` 页面
2. 选择 `Hexo Article Processor`
3. 点击 `Run workflow`，可选参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 处理数量 | 本次处理几篇文章 | `2` |
| 强制重新处理 | 重新处理已完成的文章（如需要补充封面图） | `false` |
| AI 服务提供商 | 选择 Gemini 或 OpenAI | `gemini` |
| 跳过图片迁移 | 跳过图片迁移步骤 | `false` |
| 跳过图片智能分类 | 跳过 GLM 视觉模型分类（保留所有图片） | `false` |

## AI 处理机制

### 多 Key 轮换 + 故障切换

为提高可用性，AI 处理内置了多级容错机制：

- **多 Key 轮换**：`GEMINI_API_KEY` 支持配置多个密钥（逗号分隔），单个 Key 连续失败 3 次后自动切换到下一个
- **指数退避重试**：失败后按 5s → 10s → 20s → 40s → 60s 间隔递增重试
- **自动故障切换**：当主提供商（如 Gemini）所有 Key 均不可用时，自动切换到备用提供商（OpenAI）
- **保守兜底**：所有重试和切换都失败后，文章标记为 `failed`，下次运行自动重试（最多 3 次）

### 处理后的 Front Matter 格式

```yaml
---
title: 惠州仲恺中学2024届高考百日誓师大会圆满举行
date: 2024-02-24 09:30:00
author: 惠州仲恺中学
categories:
  - 校园活动
tags:
  - 高考誓师
  - 2024届
  - 百日冲刺
  - 高三
cover: https://photo1.20080601.xyz/wx/img_xxx.webp
excerpt: 2024年2月24日，惠州仲恺中学隆重举行2024届高考百日誓师大会...
---
```

## 图片智能分类

### 概述

集成智谱 **GLM-4.1V-Thinking-Flash** 视觉理解模型，自动识别文章中的装饰性图片并移除，仅将真正有价值的内容图片上传到图床。

### 分类流程

对每篇文章中的图片，按以下流程逐篇处理（串行）：

```
提取所有图片 URL（正文 + cover 字段）
        │
        ▼
预过滤 .gif/.svg → 直接标记 DROP（不调用 AI）
        │
        ▼
下载剩余图片到内存
        │
        ▼
并发发送 base64 给 GLM（≤5 路并发）
        │
        ▼
分类完成，汇总所有 KEEP/DROP 结果
        │
        ▼
从 Markdown 中删除 DROP 的图片行
        │
        ▼
上传 KEEP 图片到图床（WebP 转换）
        │
        ▼
替换文章中的图片链接 → 写回文件
        │
        ▼
该篇文章处理完成，进入下一篇
```

### DROP 的图片类型

- 微信公众号装饰性分割线
- 关注引导图、二维码底图
- 纯色或渐变背景 banner（无实际文字或场景信息）
- 气氛渲染图（心形、星星、灯笼、花朵、飘带等纯装饰元素）
- 矢量图标、简单平面素材、emoji 大图
- 品牌水印 logo（非文章主题内容）
- 重复出现的相同或相似装饰图

### KEEP 的图片类型

- 校园活动现场照片（表彰大会、运动会、开学典礼等）
- 师生合影、个人照片、领导讲话照片
- 教学场景、课堂实拍、实验照片
- 奖状、证书、成绩展示、荣誉牌匾
- 校园环境、建筑、设施实景照片
- 包含具体文字信息的图片（标题、通知、数据图表）
- 学生作品展示、社团活动照片

### 分类策略

- `.gif` / `.svg` 后缀的图片**直接删除**，无需调用 AI（必定是装饰性图片）
- AI 分类采用**保守策略**：分类失败时默认保留图片，避免误删有价值的照片
- 下载失败的图片也默认保留，确保不丢失内容
- 分类阶段下载的图片数据会**复用**于后续上传，避免重复下载

## 图片迁移

### 配置参数

| 环境变量 / Variable | 说明 | 默认值 |
|---------------------|------|--------|
| `IMAGE_API_TOKEN` | 图床上传 API Token | — |
| `IMAGE_HOST_URL` | 图床上传地址 | `https://photo.20080601.xyz` |
| `IMAGE_DISPLAY_URL` | 图片显示域名 | `https://photo1.20080601.xyz` |
| `IMAGE_UPLOAD_CHANNEL` | 上传通道 | `telegram` |
| `IMAGE_TARGET_FOLDER` | 目标文件夹 | `wx` |

### 缓存机制

- 已上传的图片 URL 映射缓存在 `image_migration_cache.json` 中
- 相同图片不会重复上传（即使重新处理文章）
- 缓存文件会自动提交到仓库，跨次运行共享

### 图片来源

脚本会自动提取以下位置的图片 URL：
- Markdown 正文中的 `![alt](url)` 语法
- Front Matter 中的 `cover:` 字段

## 文章分类体系

| 分类 | 说明 | 示例 |
|------|------|------|
| 新闻动态 | 综合性新闻 | 学校重大新闻、对外交流 |
| 校园新闻 | 学生视角的校园生活 | 班级活动、学生日常 |
| 通知公告 | 事务性内容 | 通知、公告、报名 |
| 教务动态 | 教育教学活动 | 教学、考试、教研 |
| 校园活动 | 各类活动 | 比赛、运动会、研学 |
| 课程教学 | 课程介绍 | 课程改革、教学方法 |
| 师资力量 | 教师介绍 | 教师风采、名师介绍 |
| 办学成果 | 学校层面成果 | 成果总结、升学率 |
| 荣誉时刻 | 获奖表彰 | 获奖、表彰、优秀 |

## 状态管理

`status.json` 追踪所有文章的处理状态，支持断点续处理：

```json
{
  "total": 1050,
  "processed": 15,
  "daily_limit": 2,
  "articles": {
    "[2026-01-26]聚焦智慧教育": {
      "id": "[2026-01-26]聚焦智慧教育",
      "source": "raw-articles/[2026-01-26]聚焦智慧教育.md",
      "source_date": "2026-01-26",
      "status": "completed",
      "title": "聚焦智慧教育赋能精准教学",
      "output": "processed/2026-01-26-聚焦智慧教育.md",
      "images_migrated": true
    }
  }
}
```

文章状态流转：`pending` → `processing` → `completed` / `failed`

- `daily_limit` 每次运行时从环境变量读取最新值，不会被旧缓存覆盖
- 使用 `--force` 参数可将 `completed` 状态的文章重新纳入处理队列

## 本地测试

```bash
# 安装依赖
pip install -r scripts/requirements.txt

# 设置环境变量
export GEMINI_API_KEY="your-api-key"
export IMAGE_API_TOKEN="your-token"
export GLM_API_KEY="your-glm-key"

# 初始化状态（从 raw-articles 目录读取文章）
python scripts/status_manager.py

# 处理文章
python scripts/process_articles.py

# 图片智能分类 + 迁移
python scripts/image_migrator.py

# 质量检查
python scripts/quality_check.py
```

## 常见问题

### 如何配置多个 Gemini API Key？

在 `GEMINI_API_KEY` Secret 中用逗号分隔多个 Key：
```
AIzaSyKey1,AIzaSyKey2,AIzaSyKey3
```
系统会自动轮换使用，单个 Key 连续失败 3 次后切换到下一个。

### 为什么新文章优先处理？

系统从文件名提取日期（如 `[2026-01-26]`），按日期降序排序，确保最新的内容优先发布。

### 如何重新处理某篇文章？

**方法一**：手动触发工作流时勾选「强制重新处理」，会重新处理指定数量的已完成文章。

**方法二**：手动修改 `status.json`，将目标文章的 `status` 改为 `pending`，`attempts` 改为 `0`。

### 图片迁移失败怎么办？

- 检查 `IMAGE_API_TOKEN` 是否正确
- 查看 `image_migration_failed.json` 了解失败详情
- 失败的图片会保留原始微信链接，不影响文章其他内容
- 修复后使用 `--force` 重新处理即可，已上传的图片不会重复上传（缓存命中）

### 如何切换 AI 提供商？

手动触发工作流时在「AI 服务提供商」下拉框选择。也可通过修改 `DEFAULT_AI_PROVIDER` 环境变量更改默认值。

### 跳过图片分类但保留迁移？

手动触发时勾选「跳过图片智能分类」，图片仍会迁移到图床，但不会移除装饰性图片。

### PR 创建失败，提示权限不足？

确认 `HEXO_REPO_TOKEN`（Fine-grained PAT）对目标仓库拥有以下权限：
- `Contents`: Read and write
- `Pull requests`: Read and write

## 注意事项

1. **API 费用**：每天处理的文章数量受 `DAILY_LIMIT` 控制，建议根据 API 额度合理设置。GLM-4.1V-Thinking-Flash 目前免费，图片分类不产生额外费用。
2. **图片迁移顺序**：图片迁移在 AI 处理之后运行，且图片分类在迁移之前完成，确保只有真正有价值的图片才上传到图床，节省存储空间。
3. **人工审核**：PR 创建后请务必审核内容质量，特别是标题、分类、标签、图片链接是否正确。
4. **状态文件**：`status.json` 和 `image_migration_cache.json` 会自动提交到仓库，请勿手动删除。
5. **跨仓库架构**：本仓库（`hexo-processor-workflow`）负责处理，目标仓库（`hexo-zkzx`）负责展示，通过 PR 连接两个仓库。

## License

MIT License
