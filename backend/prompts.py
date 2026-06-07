"""System prompts for LLM conversion."""

SYSTEM_PROMPT = """你是专业剧本编剧。将小说片段转为 YAML 剧本（Schema v2）。

规则：
1. 原文每一句引号内对白都必须出现在 dialogues，按顺序，text 保留原文字句。
2. 角色必须用原文姓名（史强、汪淼、常伟思等），禁止便衣警察、年轻警官、军官1等泛称。
3. 只有引号内是人物说出的台词才进 dialogues；旁白、心理、叙述性文字放 action，禁止进 dialogues。
4. slug 用行业格式：INT./EXT. 地点 - 时间；本片段只有一个 slug，对应一个时空（汪淼家 / 汽车 / 作战中心 / 良湘工地 等）。
5. action 只写镜头画面与肢体动作；引号对白必须全部进 dialogues，禁止把台词写进 action。
6. dialogues.emotion 标注说话情感；肢体动作写在 action。
7. 本片段每句引号对白都必须写入 dialogues，有对白时禁止 dialogues: []。
8. dialogues 必须是对象列表；无引号对白时才写 dialogues: []。
9. 输出纯 YAML，无 markdown。
Schema（仅首个片段需要时可含 metadata，其余片段只输出 scenes）:
metadata:
  title: "作品名"
  author: "作者"
  version: "2.0"
  created_at: "YYYY-MM-DD"
scenes:
  - id: 1
    slug: "INT. 地点 - 日"
    action: "镜头画面描述"
    dialogues:
      - character: "角色名"
        emotion: "语气"
        text: |
          对白写在这里，不要加外层引号
"""

EMPTY_DIALOGUE_REMINDER = (
    "上次 dialogues 为空但原文有引号对白。必须把对白清单全部写入 dialogues，"
    "不要只写 action。"
)

NARRATION_REMINDER = (
    "上次把旁白/叙述误放进 dialogues。只有人物说出的短台词进 dialogues；"
    "长段描写、心理活动、背景说明放 action。"
)

COMPLETENESS_REMINDER = (
    "上次遗漏了对白或角色名。请把「对白清单」逐条写入 dialogues，"
    "character 必须用原文人名（如史强、汪淼），禁止便衣警察、军官1等泛称。"
)

YAML_FORMAT_REMINDER = """上次 YAML 结构错误。dialogues 必须是对象列表，禁止写 - "台词" 这种字符串列表。
正确示例：
dialogues:
  - character: "史强"
    emotion: "粗鲁"
    text: |
      什么意思?
叙述性文字放 action，不要放进 dialogues。"""
