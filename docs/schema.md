# 剧本 YAML Schema 设计说明（v2）

## Schema 结构预览

```yaml
metadata:
  title: "三体"
  author: "刘慈欣"
  version: "2.0"
  created_at: "2026-06-05"
scenes:
  - id: 1
    slug: "INT. 汪淼家 - 日"
    action: "门开。四名来访者站在门口——两名警察与两名陆军军官。便衣史强低头点烟，不抬眼。"
    dialogues:
      - character: "史强"
        emotion: "粗鲁"
        text: "汪淼？"
    notes: "特写：史强手中的烟头。"
```

## 相比 v1 的改进

| 维度 | v1 | v2 |
|------|----|----|
| 场景标头 | `location: 室内 - 地点 - 时间` | `slug: INT. 书房 - 日`（行业标准） |
| 台词字段 | `line` + `notes` | `text` + `emotion`（情感与台词分离） |
| 场景备注 | 无 | `notes`（拍摄/转场，与表演指导分层） |
| 元数据 | title, version | + author, created_at |

## 设计说明

### slug（场景标头）

采用 `INT.`（内景）/ `EXT.`（外景）+ 地点 + 时间的格式，与专业剧本一致，便于直接导入分镜/拍摄计划工具。

### emotion（情感标签）

从台词中提炼说话时的情感状态，便于演员把握语气，也与 `action` 中的肢体描写形成互补。

### notes（场景级）

用于拍摄提示、转场说明（如「切至」「闪回开始」「淡出」），与单句台词的表演细节区分开。

### scenes

顶层键由 `script_content` 改为 `scenes`，语义更直观。

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `metadata.title` | string | 是 | 剧本标题 |
| `metadata.author` | string | 否 | 作者，默认「佚名」 |
| `metadata.version` | string | 是 | Schema 版本，当前 `2.0` |
| `metadata.created_at` | date | 否 | 创建日期 ISO 格式 |
| `scenes[].id` | int | 是 | 场景序号 |
| `scenes[].slug` | string | 是 | 如 `INT. 书房 - 日` |
| `scenes[].action` | string | 是 | 镜头可记录的动作与环境 |
| `scenes[].dialogues[].character` | string | 是 | 角色名 |
| `scenes[].dialogues[].emotion` | string | 否 | 情感标签 |
| `scenes[].dialogues[].text` | string | 是 | 台词正文 |
| `scenes[].notes` | string | 否 | 拍摄/转场提示 |

## Pydantic 模型

见 `backend/schema.py`。
