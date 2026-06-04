# 剧本 YAML Schema 设计说明

## Schema 结构预览

```yaml
metadata:
  title: "小说标题"
  version: "1.0"
script_content:
  - scene_id: 1
    location: "室内/室外 - 地点 - 时间"
    action: "描述场景中的动作和环境。"
    dialogues:
      - character: "角色名"
        line: "台词内容"
        notes: "表演指导（可选）"
```

## 设计说明

### 场景化（Scene-based）

将剧本拆解为列表对象，便于后续通过程序直接转换为电影分镜头表格。每个 `scene_id` 对应一个可独立拍摄的单元。

### 层级清晰

将 `metadata` 与 `script_content` 分离，方便后期版本管理。`metadata.version` 可用于追踪 Schema 演进。

### 数据解耦

将 `character` 与 `line` 分开，使得 AI 可以轻松提取出所有角色列表，便于统计角色的出场频率。

### YAML 优势

相比 JSON，YAML 对人类可读性更友好，作者在 Markdown 编辑器中微调剧本时不容易出错（缩进直观）。

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `metadata.title` | string | 是 | 剧本/小说标题 |
| `metadata.version` | string | 是 | Schema 版本，默认 `1.0` |
| `script_content[].scene_id` | int | 是 | 场景序号，从 1 递增 |
| `script_content[].location` | string | 是 | 格式：`室内/室外 - 地点 - 时间` |
| `script_content[].action` | string | 是 | 镜头可记录的动作与环境 |
| `script_content[].dialogues[].character` | string | 是 | 说话角色名 |
| `script_content[].dialogues[].line` | string | 是 | 台词正文 |
| `script_content[].dialogues[].notes` | string | 否 | 表演指导、语气、动作提示 |

## Pydantic 模型

后端使用 `backend/schema.py` 中的 Pydantic 模型校验 AI 输出，确保生成的 YAML 符合 Schema 要求。

## System Prompt 基调

程序内置 System Prompt 要求 AI：

1. 严格遵守 Schema 格式输出纯 YAML
2. 将文学描述转为镜头语言（action 只写能被镜头记录的画面）
3. 保持对话完整，并根据上下文补全表演说明
