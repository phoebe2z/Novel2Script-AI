# Novel2Script AI（小说转剧本助手）

将中文小说文本自动转换为结构化 YAML 剧本（Schema v2），降低文学语言到镜头语言的改编门槛。

## 功能

- **文本输入**：粘贴多章节小说文本，可选作品名提示
- **AI 处理**：按场景切分原文，提取 action、对白、角色与 slug
- **格式导出**：输出符合 [Schema v2](docs/schema.md) 的 YAML 剧本
- **预览与下载**：前端编辑 YAML 并下载 `.yaml` 文件

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Next.js 15 + React 19 |
| 后端 | Python 3.11+ / FastAPI |
| AI | OpenAI 兼容 API（OpenAI、Groq、DeepSeek 等） |
| 校验 | Pydantic + PyYAML |

## 架构说明

```
浏览器 → Next.js (3000) → /api/convert/stream → FastAPI (8080) → LLM API
                ↑
         frontend/.env.local 中的 BACKEND_URL
```

- **API Key 只配置在后端**根目录 `.env`，不会进入前端或 Git 仓库
- 前端默认通过 Next.js 同源路由 `/api/convert/stream` 代理请求，**无需**设置 `NEXT_PUBLIC_API_URL`

## 快速开始

### 1. 克隆与配置

```bash
git clone <your-repo-url>
cd Novel2Script-AI
```

**Linux / macOS / Git Bash：**

```bash
cp .env.example .env
cp frontend/.env.local.example frontend/.env.local
```

**PowerShell：**

```powershell
Copy-Item .env.example .env
Copy-Item frontend\.env.local.example frontend\.env.local
```

编辑 `.env`，填入你自己的 `OPENAI_API_KEY`（及可选的 `OPENAI_BASE_URL`、`OPENAI_MODEL`）。

> **安全提示**：`.env` 已在 `.gitignore` 中，请勿提交真实密钥。开源前请在服务商控制台轮换已泄露的 Key。

### 2. 启动后端（端口 8080）

```bash
cd backend
py -3 -m venv .venv    # first-time only
```

**PowerShell：**

```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

**或直接运行（Windows）：**

```bat
start-backend.bat
```

健康检查：<http://localhost:8080/health>

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 <http://localhost:3000>。

## API

### `POST /api/convert/stream`（前端默认使用）

SSE 流式转换，事件类型：`start` / `progress` / `complete` / `error`。

**请求体：**

```json
{
  "novel_text": "小说正文…",
  "title_hint": "三体"
}
```

**完成事件 `complete` 字段：**

```json
{
  "yaml": "metadata:\n  title: 三体\n…",
  "metadata": { "title": "三体", "author": "刘慈欣", "version": "2.0" },
  "scene_count": 8,
  "character_count": 12,
  "source_scenes": 8
}
```

### `POST /api/convert`

非流式，一次返回完整 JSON（字段同上）。

## 项目结构

```
Novel2Script-AI/
├── backend/
│   ├── main.py           # FastAPI entrypoint
│   ├── converter.py      # LLM conversion, dialogue repair, retries
│   ├── scene_splitter.py # Rule-based scene segmentation
│   ├── schema.py         # Pydantic models
│   ├── prompts.py        # System prompts
│   └── requirements.txt
├── frontend/
│   ├── app/              # Next.js pages
│   └── app/api/convert/  # Route handlers proxying to backend
├── docs/schema.md        # Schema v2 documentation
├── .env.example          # Backend env template (safe to commit)
├── frontend/.env.local.example
└── start-backend.bat     # Windows one-click backend start
```

## 环境变量

### 后端（根目录 `.env`）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | API 密钥 | **必填** |
| `OPENAI_MODEL` | 模型名称 | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | 兼容接口地址 | OpenAI 官方 |
| `CORS_ORIGINS` | 允许的前端源 | `http://localhost:3000` |
| `CHUNK_MAX_CHARS` | 单次 LLM 请求最大字符 | `400` |
| `MAX_DIALOGUES_PER_PART` | 每片最多对白句数 | `5` |
| `OPENAI_MAX_TOKENS` | 单次输出 token 上限 | `2048` |
| `COVERAGE_MAX_RETRIES` | 对白覆盖不足重试次数 | `2` |
| `SCENE_MAX_CHARS` | 单场景切分上限 | `480` |

### 前端（`frontend/.env.local`）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BACKEND_URL` | Python 后端地址 | `http://localhost:8080` |

## Groq 示例配置

```env
OPENAI_API_KEY=gsk_xxx
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.1-8b-instant
CHUNK_MAX_CHARS=400
```

## Schema 文档

详见 [docs/schema.md](docs/schema.md)。

## 许可证

MIT
