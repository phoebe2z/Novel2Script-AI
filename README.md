# Novel2Script AI（小说转剧本助手）

在 24 小时内可交付的原型工具：将小说文本自动转换为结构化 YAML 剧本，降低文学语言到镜头语言的改编门槛。

## 功能

- **文本输入**：支持粘贴多章节小说文本
- **AI 处理**：利用 LLM 拆分场景、提取动作、对白与舞台说明
- **格式导出**：输出符合预设 YAML Schema 的剧本文件
- **预览与下载**：前端展示 YAML 预览并提供 `.yaml` 下载

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Next.js 15 + React 19 |
| 后端 | Python + FastAPI |
| AI | OpenAI API（兼容 OpenAI 格式接口） |
| 校验 | Pydantic + PyYAML |

## 快速开始

### 1. 环境配置

```bash
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 [http://localhost:3000](http://localhost:3000)。

## API

### `POST /api/convert`

**请求体：**

```json
{
  "novel_text": "小说正文...",
  "title_hint": "可选标题提示"
}
```

**响应：**

```json
{
  "yaml": "metadata:\n  title: ...",
  "metadata": { "title": "...", "version": "1.0" },
  "scene_count": 3,
  "character_count": 2
}
```

## 项目结构

```
Novel2Script-AI/
├── backend/
│   ├── main.py          # FastAPI 入口
│   ├── converter.py     # LLM 转换与重试逻辑
│   ├── schema.py        # Pydantic Schema
│   ├── prompts.py       # System Prompt
│   └── requirements.txt
├── frontend/
│   └── app/             # Next.js 页面
├── docs/
│   └── schema.md        # Schema 设计文档
└── .env.example
```

## Schema 文档

详见 [docs/schema.md](docs/schema.md)。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | API 密钥 | 必填 |
| `OPENAI_MODEL` | 模型名称 | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | 兼容接口地址 | OpenAI 官方 |
| `CORS_ORIGINS` | 允许的前端源 | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | 前端调用的 API 地址 | `http://localhost:8000` |

## 许可证

MIT
