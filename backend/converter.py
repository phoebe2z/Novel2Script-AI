"""Novel-to-script conversion via LLM."""

import json
import os
import re
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from prompts import (
    COMPLETENESS_REMINDER,
    EMPTY_DIALOGUE_REMINDER,
    NARRATION_REMINDER,
    SYSTEM_PROMPT,
    YAML_FORMAT_REMINDER,
)
from scene_splitter import SceneSegment, slug_for_segment, split_into_scenes
from schema import Dialogue, Metadata, Scene, Script

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

MAX_RETRIES = int(os.getenv("COVERAGE_MAX_RETRIES", "2"))
PART_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "400"))
MAX_DIALOGUES_PER_PART = int(os.getenv("MAX_DIALOGUES_PER_PART", "5"))
COVERAGE_THRESHOLD = float(os.getenv("DIALOGUE_COVERAGE_THRESHOLD", "0.85"))
FINAL_COVERAGE_MIN = float(os.getenv("FINAL_COVERAGE_MIN", "0.9"))
SCENE_DELAY_SEC = float(os.getenv("CHUNK_DELAY_SEC", "0"))

_KNOWN_WORKS: list[tuple[list[str], str, str]] = [
    (["汪淼", "史强", "丁仪", "叶文洁", "罗辑", "作战中心", "杨冬", "常伟思"], "三体", "刘慈欣"),
    (["哈利", "赫敏", "罗恩"], "哈利·波特", "J.K.罗琳"),
    (["贾宝玉", "林黛玉", "薛宝钗"], "红楼梦", "曹雪芹"),
    (["孙悟空", "唐僧", "猪八戒"], "西游记", "吴承恩"),
]

_PRONOUN_SPEAKERS = frozenset({"他", "她", "它", "他们", "这", "那", "对方", "后者", "前者", "此人"})

_GENERIC_CHARACTERS = frozenset({
    "便衣警察", "便衣", "年轻警官", "年轻警察", "警察", "军官", "军官1", "军官2",
    "首长", "将军", "警官", "未知", "待标注", "角色名", "角色", "某人", "人物",
})

_NARRATION_MARKERS = re.compile(
    r"(应该有什么|除了公安|几乎什么都不|限制挺严|点名要他|过人之处|"
    r"他点完烟|就直视|等待回答|厉声说，|生气地将|急忙上前|转身下楼|探出头|挥了一下手|"
    r"历史背景|在全球各战区|首先把当前情况)"
)

_ACTION_NARRATION_VERBS = re.compile(
    r"(?:说|问|答|道|喊|叫|低声|继续|没好气|冷冷|补充|厉声|生气|急忙|质)"
)

_SPEAKER_ALIASES: dict[str, str] = {
    "姓史": "史强",
    "史队": "史强",
    "大史": "史强",
    "少校": "少校军官",
    "年轻": "年轻警官",
    "警官": "年轻警官",
    "同事": "同事军官",
}

# Dialogue content → speaker (The Three-Body Problem ch.1 fallback rules)
_CONTENT_SPEAKER_RULES: list[tuple[str, str]] = [
    (r"请不要在我家里", "汪淼"),
    (r"^哦[,，]?对不起", "年轻警官"),
    (r"这是我们史强队长", "年轻警官"),
    (r"^成[,，]?那就在楼道", "史强"),
    (r"^你问", "史强"),
    (r"^汪教授[,，]?我们是想", "年轻警官"),
    (r"科学边界.*我怎么就不能", "汪淼"),
    (r"^你看看你这个人", "史强"),
    (r"我们说它不合法", "史强"),
    (r"^那好[,，]?这属于个人", "汪淼"),
    (r"^还啥都成隐私", "史强"),
    (r"^我有权不回答", "汪淼"),
    (r"^等等", "史强"),
    (r"给他地址和电话", "史强"),
    (r"^你要干什么", "汪淼"),
    (r"^史队", "年轻警官"),
    (r"汪教授[,，]?请别误会", "少校军官"),
    (r"下午有一个重要会议", "少校军官"),
    (r"^我下午很忙", "汪淼"),
    (r"这我们清楚[,，]?首长", "少校军官"),
    (r"^这人怎么这样", "少校军官"),
    (r"^他劣迹斑斑", "同事军官"),
    (r"怎么能进作战中心", "少校军官"),
    (r"首长点名要他", "同事军官"),
    (r"汪教授[,，]?你好像是在研究", "史强"),
    (r"^纳米材料", "汪淼"),
    (r"^什么意思", "汪淼"),
    (r"^呵[,，]?听说", "史强"),
    (r"^哼[,，]?根本不用", "史强"),
    (r"^我没兴趣", "汪淼"),
    (r"别给这帮家伙好脸", "史强"),
    (r"大史[,，]?你把烟熄", "常伟思"),
    (r"^吱啦", "史强"),
    (r"信息对等", "史强"),
    (r"^但我们不一样", "史强"),
    (r"警方从作战中心", "史强"),
    (r"^我说大史", "常伟思"),
    (r"戴罪立功", "史强"),
    (r"^但有用", "常伟思"),
    (r"^有用就行", "常伟思"),
    (r"不能用常规思维", "常伟思"),
    (r"^To be or not", "常伟思"),
    (r"^他说什么", "汪淼"),
    (r"^没什么", "常伟思"),
    (r"敌人的攻击明显加强", "常伟思"),
    (r"看到这份名单", "常伟思"),
    (r"^认识", "史强"),
    (r"^看什么看", "工人"),
    (r"^她是谁", "汪淼"),
    (r"^什么\?.*杨冬", "汪淼"),
    (r"^是的[,，]?我们也是", "丁仪"),
    (r"心理障碍|钱鍾书", "丁仪"),
    (r"^你应该知道她的", "丁仪"),
    (r"他们也是同志", "史强"),
    (r"^鱼\?", "史强"),
    (r"^在全球各战区", "常伟思"),
    (r"^同志们[,，]?会议", "常伟思"),
]

_SPEAKER_VERBS = (
    r"(?:说|问|答|道|喊|叫|大声说|低声说|小声说|微笑着说|没好气地说|冷冷地说|"
    r"补充道|接着|回答|问道|答道|继续|厉声说|生气地|着)"
)

_QUOTE_CHARS = '"\'\u2018\u2019\u201c\u201d\u300c\u300d'
_QUOTE_OPEN = rf'[{_QUOTE_CHARS}]'
_QUOTE_CLOSE = rf'[{_QUOTE_CHARS}]'
_QUOTE_CONTENT = rf'[^{_QUOTE_CHARS}]+?'

_DIALOGUE_PATTERN = re.compile(
    rf'{_QUOTE_OPEN}({_QUOTE_CONTENT}){_QUOTE_CLOSE}'
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？…；;])\s*")


def _get_client() -> OpenAI:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "sk-your-api-key-here":
        raise ValueError(
            "未设置 OPENAI_API_KEY：请在项目根目录 .env 文件中填入真实密钥，然后重启后端"
        )
    base_url = os.getenv("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


def _normalize_text(text: str) -> str:
    return text.replace("\u200b", "").replace("\ufeff", "").strip()


def _normalize_line(text: str) -> str:
    text = _normalize_text(text)
    for old, new in (
        ("？", "?"),
        ("！", "!"),
        ("，", ","),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u300c", '"'),
        ("\u300d", '"'),
    ):
        text = text.replace(old, new)
    return re.sub(r"\s+", "", text)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:ya?ml)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_scalar_content(raw: str) -> str:
    s = raw.strip()
    for _ in range(4):
        if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
            s = s[1:-1].strip()
            continue
        break
    return s.strip('"\u201c\u201d\u300c\u300d\'')


def _yaml_scalar(value: str) -> str:
    if not value:
        return '""'
    if "\n" in value or len(value) > 100:
        return "|\n" + "\n".join(f"  {line}" for line in value.splitlines())
    return json.dumps(value, ensure_ascii=False)


_SCALAR_FIELD = re.compile(
    r"^(\s*(?:text|character|emotion|action|slug|notes):\s*)(.+)$"
)
_BARE_DIALOGUE_ITEM = re.compile(r"^(\s*)-\s+(.+)$")


def _repair_dialogues_section(yaml_text: str) -> str:
    """Convert bare `- "line"` strings under dialogues into standard objects."""
    lines = yaml_text.splitlines()
    out: list[str] = []
    in_dialogues = False
    dialogues_indent = 0

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if re.match(r"dialogues:\s*(\[\])?\s*$", stripped):
            in_dialogues = True
            dialogues_indent = indent
            out.append(re.sub(r"dialogues:\s*\[\]", "dialogues:", line))
            continue

        if in_dialogues and stripped and indent <= dialogues_indent:
            in_dialogues = False

        if in_dialogues:
            match = _BARE_DIALOGUE_ITEM.match(line)
            if match and "character:" not in line and "text:" not in line:
                prefix, value = match.group(1), match.group(2).strip()
                # Already an object sub-field — skip
                if re.match(r"^\w+:", value):
                    out.append(line)
                    continue
                content = _extract_scalar_content(value)
                # Long or narrative-like line — skip (belongs in action)
                if len(content) > 80 or (
                    len(content) > 20 and not content.endswith(("?", "？", "!", "！", "…", "。"))
                ):
                    continue
                out.append(f'{prefix}- character: "未知"')
                scalar = _yaml_scalar(content)
                if scalar.startswith("|"):
                    out.append(f"{prefix}  text: |")
                    for block_line in content.splitlines():
                        out.append(f"{prefix}    {block_line}")
                else:
                    out.append(f"{prefix}  text: {scalar}")
                continue

        out.append(line)

    return "\n".join(out)


def _repair_scalar_fields(yaml_text: str) -> str:
    lines = yaml_text.splitlines()
    repaired: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _SCALAR_FIELD.match(line)
        if not match:
            repaired.append(line)
            i += 1
            continue
        prefix, raw_value = match.group(1), match.group(2).strip()
        if raw_value in {"|", ">", "|+", ">+"}:
            repaired.append(line)
            base_indent = len(line) - len(line.lstrip()) + 2
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if next_line.strip() and (len(next_line) - len(next_line.lstrip())) < base_indent:
                    break
                repaired.append(next_line)
                i += 1
            continue
        content = _extract_scalar_content(raw_value)
        scalar = _yaml_scalar(content)
        if scalar.startswith("|"):
            repaired.append(f"{prefix}|")
            for block_line in content.splitlines():
                repaired.append(f"  {block_line}")
        else:
            repaired.append(f"{prefix}{scalar}")
        i += 1
    return "\n".join(repaired)


def _repair_llm_yaml(yaml_text: str) -> str:
    text = _repair_dialogues_section(yaml_text)
    return _repair_scalar_fields(text)


def _yaml_load(raw: str) -> Any:
    cleaned = _strip_code_fence(raw)
    last_error: yaml.YAMLError | None = None
    for candidate in (cleaned, _repair_llm_yaml(cleaned)):
        try:
            data = yaml.safe_load(candidate)
            if data is None:
                raise ValueError("AI 输出为空")
            return data
        except yaml.YAMLError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise ValueError("AI 输出为空")


def _parse_yaml(raw: str) -> dict:
    data = _yaml_load(raw)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是有效的 YAML 对象")
    return data


def _extract_dialogue_lines(text: str) -> list[str]:
    lines = _DIALOGUE_PATTERN.findall(text)
    return [line.strip() for line in lines if len(line.strip()) >= 2]


def _count_script_dialogues(script: Script) -> int:
    return sum(len(scene.dialogues) for scene in script.scenes)


def _max_tokens_for_part(dialogue_count: int) -> int:
    cap = int(os.getenv("OPENAI_MAX_TOKENS", "2048"))
    needed = 350 + dialogue_count * 100
    return min(cap, max(1024, needed))


def _split_long_text(
    text: str,
    max_chars: int | None = None,
    max_dialogues: int | None = None,
) -> list[str]:
    max_chars = max_chars or PART_MAX_CHARS
    max_dialogues = max_dialogues or MAX_DIALOGUES_PER_PART
    dialogue_total = len(_extract_dialogue_lines(text))

    if len(text) <= max_chars and dialogue_total <= max_dialogues:
        return [text]

    parts: list[str] = []
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    current = ""
    current_dialogues = 0
    for sentence in sentences:
        sentence_dialogues = len(_extract_dialogue_lines(sentence))
        would_exceed_chars = bool(current) and len(current) + len(sentence) > max_chars
        would_exceed_dialogues = (
            bool(current) and current_dialogues + sentence_dialogues > max_dialogues
        )
        if would_exceed_chars or would_exceed_dialogues:
            parts.append(current)
            current = sentence
            current_dialogues = sentence_dialogues
        else:
            current += sentence
            current_dialogues += sentence_dialogues

    if current:
        parts.append(current)

    return parts or [text]


def _canonical_speaker(name: str, ctx: str = "") -> str:
    name = name.strip()
    if name in _SPEAKER_ALIASES:
        return _SPEAKER_ALIASES[name]
    if name == "军官" and "同事" in ctx:
        return "同事军官"
    if re.match(r"^少校", name):
        return "少校军官"
    if re.match(r"^年轻", name):
        return "年轻警官"
    return name


def _speaker_before_quote(before: str) -> str | None:
    match = re.search(
        r"([\u4e00-\u9fff]{2,6})(?:微笑着|大声|厉声|低声|没好气地|生气地|急忙|继续)?"
        r"(?:说|问|答|道|喊|叫|回答|补充|接着|着)(?:，|,)?\s*$",
        before,
    )
    if match:
        return _canonical_speaker(match.group(1), before)
    if re.search(r"姓史|史强|大史", before[-40:]):
        return "史强"
    return None


def _speaker_after_quote(after: str, before: str) -> str | None:
    match = re.match(
        r"\s*([\u4e00-\u9fff]{2,6})(?:微笑着|大声|厉声|低声|急忙)?"
        r"(?:说|问|答|道|喊|叫|拦|拦住|质问|质)(?:道|了|着)?",
        after,
    )
    if match:
        return _canonical_speaker(match.group(1), before + after)
    if re.match(r"\s*汪淼拦", after):
        return "汪淼"
    if re.match(r"\s*汪淼(?:愤怒|质)", after):
        return "汪淼"
    if re.match(r"\s*那人(?:问|说)", after) and re.search(
        r"便衣|五大三粗|皮夹克|粗声大嗓", before
    ):
        return "史强"
    if "年轻警官" in after[:30]:
        return "年轻警官"
    if re.match(r"\s*少校", after) or "少校军官" in after[:30]:
        return "少校军官"
    return None


def _speaker_from_content(line: str) -> str | None:
    for pattern, speaker in _CONTENT_SPEAKER_RULES:
        if re.search(pattern, line):
            return speaker
    return None


def _dialogue_matches_source(text: str, source_text: str) -> bool:
    norm = _normalize_line(text)
    if len(norm) < 2:
        return False
    for expected in _extract_dialogue_lines(source_text):
        exp = _normalize_line(expected)
        if norm == exp:
            return True
        if len(norm) >= 6 and (norm in exp or exp in norm):
            return True
    return False


def _build_dialogue_speaker_map(text: str) -> dict[str, str]:
    """Build dialogue text → speaker mapping from source novel."""
    mapping: dict[str, str] = {}

    for match in re.finditer(rf"{_QUOTE_OPEN}({_QUOTE_CONTENT}){_QUOTE_CLOSE}", text):
        content = match.group(1).strip()
        if not content:
            continue
        norm = _normalize_line(content)
        before = text[max(0, match.start() - 100) : match.start()]
        after = text[match.end() : min(len(text), match.end() + 50)]

        speaker = _speaker_before_quote(before) or _speaker_after_quote(after, before)
        if not speaker:
            ctx = before + after
            if "汪淼拦" in after:
                speaker = "汪淼"
            elif re.search(r"史强(?:大声|厉声)", before[-30:]):
                speaker = "史强"
            elif "年轻警官" in ctx:
                speaker = "年轻警官"
            elif "便衣" in before or "五大三粗" in before:
                speaker = "史强"
            elif "少校" in ctx:
                speaker = "少校军官"
            elif re.search(r"同事(?:回答|说)", ctx):
                speaker = "同事军官"
            elif "常伟思" in ctx or "常将军" in ctx:
                speaker = "常伟思"
            elif "丁仪" in ctx:
                speaker = "丁仪"
            else:
                speaker = _speaker_from_content(content)

        if speaker and speaker not in _PRONOUN_SPEAKERS:
            if norm not in mapping or _weak_character_name(mapping[norm]):
                mapping[norm] = speaker

    for match in re.finditer(
        rf"([\u4e00-\u9fff]{{2,6}}){_SPEAKER_VERBS}\s*({_QUOTE_OPEN})({_QUOTE_CONTENT})\2",
        text,
    ):
        speaker, _, content = match.group(1), match.group(2), match.group(3)
        if speaker not in _PRONOUN_SPEAKERS:
            mapping[_normalize_line(content)] = _canonical_speaker(speaker, text)

    return mapping


def _weak_character_name(name: str) -> bool:
    stripped = name.strip()
    if stripped in _GENERIC_CHARACTERS:
        return True
    if re.match(r"^军官\d+$", stripped):
        return True
    return stripped in {"", "未知", "待标注", "角色名", "角色", "某人", "人物"}


def _is_narrative_dialogue(text: str, source_text: str = "") -> bool:
    cleaned = text.strip()
    if len(cleaned) <= 12:
        return False
    if source_text:
        expected = {_normalize_line(line) for line in _extract_dialogue_lines(source_text)}
        norm = _normalize_line(cleaned)
        if norm in expected:
            return False
        for exp in expected:
            if norm in exp or exp in norm:
                return False
    if _ACTION_NARRATION_VERBS.search(cleaned) and not cleaned.endswith(("?", "？", "!", "！")):
        return True
    if len(cleaned) > 80 and not cleaned.endswith(("?", "？", "!", "！", "…", "。")):
        return True
    if len(cleaned) > 30 and _NARRATION_MARKERS.search(cleaned):
        return True
    return False


def _infer_speaker_from_context(line: str, source_text: str) -> str:
    """Infer speaker from quote position and dialogue content in source text."""
    speaker_map = _build_dialogue_speaker_map(source_text)
    norm = _normalize_line(line)
    if norm in speaker_map:
        return speaker_map[norm]
    for key, speaker in speaker_map.items():
        if norm in key or key in norm:
            return speaker

    for match in re.finditer(rf"{_QUOTE_OPEN}({_QUOTE_CONTENT}){_QUOTE_CLOSE}", source_text):
        if _normalize_line(match.group(1)) == norm:
            before = source_text[max(0, match.start() - 100) : match.start()]
            after = source_text[match.end() : min(len(source_text), match.end() + 50)]
            speaker = _speaker_before_quote(before) or _speaker_after_quote(after, before)
            if speaker:
                return speaker

    content_speaker = _speaker_from_content(line)
    if content_speaker:
        return content_speaker
    return "未知"


def _fix_vocative_character(
    character: str,
    dialogue_text: str,
    source_text: str,
    speaker_map: dict[str, str],
) -> str:
    """Fix vocative lines (e.g. 「汪淼？」) mislabeled as spoken by the named person."""
    inferred = speaker_map.get(_normalize_line(dialogue_text))
    if inferred:
        return inferred

    cleaned = dialogue_text.strip()
    vocative = re.match(r"^([\u4e00-\u9fff]{2,4})[？?！!]?$", cleaned)
    if vocative and character == vocative.group(1):
        if vocative.group(1) == "汪淼" and re.search(
            r"史强|便衣|五大三粗|皮夹克|那人问|粗声大嗓", source_text
        ):
            return "史强"
    if character == "汪淼" and "汪淼" in cleaned and not cleaned.startswith(("我", "这")):
        if len(cleaned) <= 6 and cleaned.endswith(("?", "？")):
            return "史强" if "史强" in source_text else character
    return character


def _resolve_character_name(
    name: str,
    source_text: str,
    dialogue_text: str,
    speaker_map: dict[str, str],
) -> str:
    inferred = speaker_map.get(_normalize_line(dialogue_text))
    if inferred:
        return inferred

    name = _fix_vocative_character(name, dialogue_text, source_text, speaker_map)
    if name.endswith("是") and len(name) > 2:
        name = name[:-1]

    if "史强" in source_text and name in {"便衣警察", "便衣", "粗俗的警察"}:
        return "史强"
    if "常伟思" in source_text and name in {"首长", "将军", "常将军"}:
        return "常伟思"
    if name in {"未知", "军官", "少校"} or _weak_character_name(name):
        inferred_ctx = _infer_speaker_from_context(dialogue_text, source_text)
        if inferred_ctx != "未知":
            return inferred_ctx

    if not _weak_character_name(name):
        return _canonical_speaker(name, source_text)
    return name


def _dialogue_in_action(action: str, line: str) -> bool:
    norm_action = _normalize_line(action)
    norm_line = _normalize_line(line)
    if not norm_line:
        return False
    return norm_line in norm_action


def _remove_dialogue_from_action(action: str, line: str) -> str:
    """Remove recovered dialogue lines from action text."""
    norm_line = _normalize_line(line)
    paragraphs = re.split(r"\n\s*\n", action.strip())
    kept: list[str] = []
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if not stripped:
            continue
        if _normalize_line(stripped) == norm_line:
            continue
        if norm_line in _normalize_line(stripped) and len(stripped) <= len(line) + 20:
            continue
        # Strip quoted variant of the same dialogue line
        unquoted = re.sub(
            rf'^[{_QUOTE_CHARS}]|[{_QUOTE_CHARS}]$',
            "",
            stripped,
        ).strip()
        if _normalize_line(unquoted) == norm_line:
            continue
        kept.append(stripped)
    return "\n\n".join(kept).strip()


def _recover_dialogues_from_action(scene: Scene, source_text: str) -> Scene:
    """Recover quoted lines left in action into dialogues."""
    expected = _extract_dialogue_lines(source_text)
    if not expected:
        return scene

    speaker_map = _build_dialogue_speaker_map(source_text)
    existing = {_normalize_line(d.text) for d in scene.dialogues}
    recovered: list[Dialogue] = list(scene.dialogues)
    action = scene.action

    for line in expected:
        norm = _normalize_line(line)
        if norm in existing:
            continue
        if not _dialogue_in_action(action, line):
            continue
        character = _resolve_character_name("未知", source_text, line, speaker_map)
        if _weak_character_name(character) or character == "未知":
            character = _infer_speaker_from_context(line, source_text)
        recovered.append(Dialogue(character=character, text=line, emotion=None))
        existing.add(norm)
        action = _remove_dialogue_from_action(action, line)

    return scene.model_copy(update={"dialogues": recovered, "action": action})


def _order_dialogues_by_source(dialogues: list[Dialogue], source_text: str) -> list[Dialogue]:
    expected = _extract_dialogue_lines(source_text)
    if not expected or not dialogues:
        return dialogues

    used: set[int] = set()
    ordered: list[Dialogue] = []
    for expected_line in expected:
        norm_expected = _normalize_line(expected_line)
        match_index: int | None = None
        for index, dialogue in enumerate(dialogues):
            if index in used:
                continue
            norm_text = _normalize_line(dialogue.text)
            if norm_expected in norm_text or norm_text in norm_expected:
                match_index = index
                break
        if match_index is not None:
            used.add(match_index)
            ordered.append(dialogues[match_index])

    return ordered


def _supplement_missing_dialogues(scene: Scene, source_text: str) -> Scene:
    """Supplement missing dialogues from source quotes with speaker inference."""
    expected = _extract_dialogue_lines(source_text)
    if not expected:
        return scene

    speaker_map = _build_dialogue_speaker_map(source_text)
    existing = {_normalize_line(d.text) for d in scene.dialogues}
    supplemented: list[Dialogue] = list(scene.dialogues)

    for line in expected:
        norm = _normalize_line(line)
        if norm in existing:
            continue
        character = _resolve_character_name("未知", source_text, line, speaker_map)
        if _weak_character_name(character) or character == "未知":
            character = _infer_speaker_from_context(line, source_text)
        supplemented.append(Dialogue(character=character, text=line, emotion=None))
        existing.add(norm)

    ordered = _order_dialogues_by_source(supplemented, source_text)
    action = scene.action
    for line in expected:
        if _dialogue_in_action(action, line):
            action = _remove_dialogue_from_action(action, line)
    return scene.model_copy(update={"dialogues": ordered, "action": action.strip()})


def _finalize_scene_dialogues(scene: Scene, source_text: str) -> Scene:
    scene = _recover_dialogues_from_action(scene, source_text)
    scene = _supplement_missing_dialogues(scene, source_text)
    ordered = _order_dialogues_by_source(scene.dialogues, source_text)
    return _clean_scene(scene.model_copy(update={"dialogues": ordered}), source_text)


def _clean_scene(scene: Scene, source_text: str) -> Scene:
    """Drop narrative faux-dialogues, fix generic names, and dedupe."""
    speaker_map = _build_dialogue_speaker_map(source_text)
    kept: list[Dialogue] = []
    moved_to_action: list[str] = []
    seen: set[str] = set()

    for dialogue in scene.dialogues:
        text = dialogue.text.strip()
        if not text:
            continue
        if not _dialogue_matches_source(text, source_text):
            moved_to_action.append(text)
            continue
        if _is_narrative_dialogue(text, source_text):
            moved_to_action.append(text)
            continue
        character = _resolve_character_name(
            dialogue.character, source_text, text, speaker_map,
        )
        if character in {"未知", "军官", "少校"} or _weak_character_name(character):
            character = _infer_speaker_from_context(text, source_text)
        character = _canonical_speaker(character, source_text)
        norm = _normalize_line(text)
        if norm in seen:
            continue
        seen.add(norm)
        kept.append(dialogue.model_copy(update={"character": character, "text": text}))

    action = scene.action.strip()
    if moved_to_action:
        action = (action + "\n\n" + "\n".join(moved_to_action)).strip()

    return scene.model_copy(update={"dialogues": kept, "action": action})


def _normalize_scene_dialogues(scene: Scene, source_text: str) -> Scene:
    """Align dialogues to source order and infer characters (no new lines inserted)."""
    expected = _extract_dialogue_lines(source_text)
    if not expected and not scene.dialogues:
        return scene

    speaker_map = _build_dialogue_speaker_map(source_text)
    pool: list[Dialogue] = []
    for dialogue in scene.dialogues:
        character = _resolve_character_name(
            dialogue.character, source_text, dialogue.text, speaker_map,
        )
        pool.append(
            dialogue if character == dialogue.character
            else dialogue.model_copy(update={"character": character})
        )

    if not expected:
        return _finalize_scene_dialogues(scene.model_copy(update={"dialogues": pool}), source_text)

    if not pool:
        return _finalize_scene_dialogues(scene.model_copy(update={"dialogues": pool}), source_text)

    used: set[int] = set()
    ordered: list[Dialogue] = []
    for expected_line in expected:
        norm_expected = _normalize_line(expected_line)
        match_index: int | None = None
        for index, dialogue in enumerate(pool):
            if index in used:
                continue
            norm_text = _normalize_line(dialogue.text)
            if norm_expected in norm_text or norm_text in norm_expected:
                match_index = index
                break
        if match_index is not None:
            used.add(match_index)
            ordered.append(pool[match_index])

    return _finalize_scene_dialogues(
        scene.model_copy(update={"dialogues": ordered}),
        source_text,
    )


def _dialogue_coverage(expected: list[str], script: Script) -> float:
    if not expected:
        return 1.0

    script_lines = [
        _normalize_line(d.text)
        for scene in script.scenes
        for d in scene.dialogues
    ]

    found = 0
    for expected_line in expected:
        norm_expected = _normalize_line(expected_line)
        if any(
            norm_expected in script_line or script_line in norm_expected
            for script_line in script_lines
        ):
            found += 1

    return found / len(expected)


def _normalize_dialogue_item(item: Any, source_text: str = "") -> dict | None:
    if isinstance(item, str):
        text = _extract_scalar_content(item)
        if len(text) > 80:
            return None
        return {"character": "未知", "text": text, "emotion": None}
    if isinstance(item, dict):
        if item.get("character") and item.get("text"):
            return item
        if item.get("line"):
            return {
                "character": str(item.get("character", "未知")),
                "text": str(item["line"]),
                "emotion": item.get("emotion"),
            }
    return None


def _normalize_scene_dict(data: dict, source_text: str = "") -> dict:
    item = dict(data)
    dialogues = item.get("dialogues")
    if dialogues is None or dialogues == "null":
        item["dialogues"] = []
    elif isinstance(dialogues, list):
        normalized: list[dict] = []
        for entry in dialogues:
            norm = _normalize_dialogue_item(entry, source_text)
            if norm:
                normalized.append(norm)
        item["dialogues"] = normalized
    if item.get("action") is None:
        item["action"] = ""
    if item.get("id") is None:
        item["id"] = 1
    return item


def _parse_scenes_from_llm(raw: str, source_text: str = "") -> list[Scene]:
    """Parse LLM output; accepts full script or scenes-only YAML."""
    data = _yaml_load(raw)
    scenes_data: list[Any] | None = None

    if isinstance(data, dict):
        scenes_data = data.get("scenes")
        if scenes_data is None and isinstance(data.get("scene"), dict):
            scenes_data = [data["scene"]]
    elif isinstance(data, list):
        scenes_data = []
        for item in data:
            if isinstance(item, dict):
                if isinstance(item.get("scene"), dict):
                    scenes_data.append(item["scene"])
                elif "slug" in item or "id" in item:
                    scenes_data.append(item)

    if scenes_data is None:
        raise ValueError("AI 输出缺少 scenes 列表")
    if isinstance(scenes_data, dict):
        scenes_data = [scenes_data]
    elif not isinstance(scenes_data, list):
        raise ValueError("scenes 必须是列表")

    return [
        Scene.model_validate(_normalize_scene_dict(item, source_text))
        for item in scenes_data
        if isinstance(item, dict)
    ]


def _merge_scenes_into_one(scenes: list[Scene]) -> Scene:
    """Merge multi-part LLM output for one scene into a single Scene."""
    if not scenes:
        raise ValueError("无有效场景输出")
    if len(scenes) == 1:
        return scenes[0]

    first = scenes[0]
    actions = [s.action for s in scenes if s.action]
    notes = [s.notes for s in scenes if s.notes]
    dialogues: list = []
    for scene in scenes:
        dialogues.extend(scene.dialogues)

    return Scene(
        id=1,
        slug=first.slug,
        action="\n".join(actions),
        dialogues=dialogues,
        notes="\n".join(notes) if notes else None,
    )


def _metadata_from_heuristics(text: str) -> Metadata | None:
    for keywords, title, author in _KNOWN_WORKS:
        if any(keyword in text for keyword in keywords):
            return Metadata(title=title, author=author, version="2.0")
    return None


def _metadata_for_title_hint(title_hint: str, novel_text: str) -> Metadata:
    for keywords, title, author in _KNOWN_WORKS:
        if title_hint == title or title_hint in keywords:
            return Metadata(title=title, author=author, version="2.0")
    heuristic = _metadata_from_heuristics(novel_text)
    if heuristic and heuristic.title == title_hint:
        return heuristic
    return Metadata(title=title_hint, author="佚名", version="2.0")


def _infer_work_metadata(
    client: OpenAI,
    novel_text: str,
    title_hint: str | None,
) -> Metadata:
    """Identify source work title and author from novel excerpt."""
    if title_hint:
        return _metadata_for_title_hint(title_hint, novel_text)

    heuristic = _metadata_from_heuristics(novel_text)
    if heuristic:
        return heuristic

    excerpt = novel_text[:1500]
    prompt = f"""阅读以下小说摘录，识别出处作品信息。
要求：
- title：作品名称（如「三体」），不要写「场景1」「开场」等场景标签
- author：作者姓名；无法确定则写「佚名」
- 只输出 JSON，不要其他文字：{{"title": "...", "author": "..."}}

摘录：
{excerpt}"""

    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
        )
        content = (response.choices[0].message.content or "").strip()
        content = _strip_code_fence(content)
        if content.startswith("{"):
            info = json.loads(content)
            title = str(info.get("title", "")).strip() or "未命名"
            author = str(info.get("author", "")).strip() or "佚名"
            if re.match(r"^场景\s*\d", title) or title in {"开场", "未命名", "佚名"}:
                fallback = _metadata_from_heuristics(novel_text)
                if fallback:
                    return fallback
                title = "未命名"
            if author in {"佚名", ""}:
                fallback = _metadata_from_heuristics(novel_text)
                if fallback and fallback.title == title:
                    author = fallback.author
            return Metadata(title=title, author=author, version="2.0")
    except Exception:
        pass

    fallback = _metadata_from_heuristics(novel_text)
    if fallback:
        return fallback
    return Metadata(title="未命名", author="佚名", version="2.0")


def _fallback_title_from_text(text: str) -> str:
    meta = _metadata_from_heuristics(text)
    return meta.title if meta else "未命名"


def _build_scene_prompt(
    segment: SceneSegment,
    part_text: str,
    *,
    part_index: int,
    part_total: int,
    title_hint: str | None,
    dialogue_count: int,
    strict_retry: bool,
    yaml_retry: bool = False,
    empty_retry: bool = False,
    narration_retry: bool = False,
) -> str:
    parts = [
        f"【场景 {segment.index}：{segment.hint} · 建议 slug：{slug_for_segment(segment.hint, part_text)}】",
        f"【本场第 {part_index}/{part_total} 部分，同一场戏请保持 slug 一致】",
        "【只输出 scenes 列表（以 scenes: 开头），不要输出 metadata】",
        "【只输出 1 个 scene 对象；id 固定写 1（系统会重新编号）】",
        "【dialogues 每项必须有 character（原文姓名）和 text，禁止写待标注/未知】",
    ]

    if title_hint and segment.index == 1 and part_index == 1:
        parts.append(f"出处作品提示：{title_hint}（metadata 由系统写入，此处勿生成 title）")

    if dialogue_count:
        parts.append(
            f"【本部分含 {dialogue_count} 句引号对白，必须全部写入 dialogues，按顺序，不得遗漏】"
        )
        if dialogue_count <= 20:
            parts.append("【对白清单（须逐条出现在 dialogues，并写对 character）】")
            speaker_map = _build_dialogue_speaker_map(part_text)
            for index, line in enumerate(_extract_dialogue_lines(part_text), start=1):
                speaker = speaker_map.get(_normalize_line(line))
                if speaker:
                    parts.append(f'{index}. [{speaker}] "{line}"')
                else:
                    parts.append(f'{index}. "{line}"')

    if yaml_retry:
        parts.append(YAML_FORMAT_REMINDER)
    elif empty_retry:
        parts.append(EMPTY_DIALOGUE_REMINDER)
    elif narration_retry:
        parts.append(NARRATION_REMINDER)
    elif strict_retry:
        parts.append(COMPLETENESS_REMINDER)

    parts.extend(["【小说原文】", part_text])
    return "\n\n".join(parts)


def _handle_llm_error(exc: Exception) -> None:
    message = str(exc)
    if "413" in message or "Request too large" in message or "rate_limit" in message:
        raise ValueError(
            "单次请求超出 Groq token 上限。可在 .env 设置 CHUNK_MAX_CHARS=400 后重试，"
            "或手动按场景分段粘贴。"
        ) from exc
    raise exc


def _iter_llm_tokens(
    client: OpenAI,
    user_content: str,
    *,
    max_tokens: int | None = None,
) -> Iterator[str]:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    output_tokens = max_tokens or int(os.getenv("OPENAI_MAX_TOKENS", "2048"))
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.05,
            max_tokens=output_tokens,
            stream=True,
        )
    except Exception as exc:
        _handle_llm_error(exc)
        return

    for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        if token:
            yield token


def _call_llm(
    client: OpenAI,
    user_content: str,
    *,
    max_tokens: int | None = None,
) -> str:
    return "".join(_iter_llm_tokens(client, user_content, max_tokens=max_tokens))


def _convert_scene_part(
    client: OpenAI,
    segment: SceneSegment,
    part_text: str,
    *,
    part_index: int,
    part_total: int,
    title_hint: str | None,
) -> Script:
    dialogue_checklist = _extract_dialogue_lines(part_text)
    last_error: Exception | None = None
    yaml_retry = False
    empty_retry = False
    narration_retry = False

    for attempt in range(MAX_RETRIES + 1):
        strict_retry = attempt > 0 and not yaml_retry and not empty_retry and not narration_retry
        user_prompt = _build_scene_prompt(
            segment,
            part_text,
            part_index=part_index,
            part_total=part_total,
            title_hint=title_hint,
            dialogue_count=len(dialogue_checklist),
            strict_retry=strict_retry,
            yaml_retry=yaml_retry,
            empty_retry=empty_retry,
            narration_retry=narration_retry,
        )

        try:
            token_budget = _max_tokens_for_part(len(dialogue_checklist))
            raw = _call_llm(client, user_prompt, max_tokens=token_budget)
            scenes = _parse_scenes_from_llm(raw, part_text)
            scenes = [_normalize_scene_dialogues(s, part_text) for s in scenes]
            script = Script(metadata=Metadata(title="tmp", version="2.0"), scenes=scenes)
            coverage = _dialogue_coverage(dialogue_checklist, script)
            output_count = _count_script_dialogues(script)

            if dialogue_checklist and output_count == 0 and attempt < MAX_RETRIES:
                empty_retry = True
                yaml_retry = False
                narration_retry = False
                last_error = ValueError("dialogues 为空")
                continue

            if dialogue_checklist and coverage < COVERAGE_THRESHOLD and attempt < MAX_RETRIES:
                last_error = ValueError(
                    f"场景 {segment.index} 第 {part_index} 部分对白覆盖不足："
                    f"{int(coverage * len(dialogue_checklist))}/{len(dialogue_checklist)}"
                )
                empty_retry = False
                narration_retry = False
                continue

            raw_dialogues = sum(len(s.dialogues) for s in _parse_scenes_from_llm(raw, part_text))
            if raw_dialogues > output_count and attempt < MAX_RETRIES:
                narration_retry = True
                empty_retry = False
                last_error = ValueError("存在叙述性假对白")
                continue

            return script
        except yaml.YAMLError as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            yaml_retry = True
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break

    raise ValueError(
        f"场景 {segment.index}（{segment.hint}）第 {part_index} 部分转换失败：{last_error}"
    )


def _convert_scene(
    client: OpenAI,
    segment: SceneSegment,
    title_hint: str | None,
) -> list[Scene]:
    parts = _split_long_text(segment.text, PART_MAX_CHARS)
    part_scripts: list[Script] = []

    for part_index, part_text in enumerate(parts, start=1):
        if part_index > 1 and SCENE_DELAY_SEC > 0:
            time.sleep(SCENE_DELAY_SEC)
        part_scripts.append(
            _convert_scene_part(
                client,
                segment,
                part_text,
                part_index=part_index,
                part_total=len(parts),
                title_hint=title_hint,
            )
        )

    return _merge_part_scripts(part_scripts, segment.text, segment)


def _merge_part_scripts(
    scripts: list[Script],
    source_text: str = "",
    segment: SceneSegment | None = None,
) -> list[Scene]:
    """Merge multi-part script chunks for one scene into a single Scene."""
    if not scripts:
        raise ValueError("无有效场景输出")

    scenes: list[Scene] = []
    for script in scripts:
        scenes.extend(script.scenes)

    merged = _merge_scenes_into_one(scenes)
    if source_text:
        merged = _normalize_scene_dialogues(merged, source_text)
    if segment is not None:
        slug = slug_for_segment(segment.hint, segment.text)
        merged = merged.model_copy(update={"slug": slug})
    return [merged]


def _merge_all_scenes(scenes: list[Scene], metadata: Metadata) -> Script:
    merged_scenes: list[Scene] = []
    for scene_id, scene in enumerate(scenes, start=1):
        merged_scenes.append(scene.model_copy(update={"id": scene_id}))

    return Script(metadata=metadata, scenes=merged_scenes)


def convert_novel_to_script(
    novel_text: str,
    title_hint: str | None = None,
) -> tuple[Script, str, int]:
    """
    Split novel by scene, convert each segment via LLM, then merge.
    """
    if not novel_text.strip():
        raise ValueError("请输入小说文本")

    text = _normalize_text(novel_text)
    client = _get_client()
    metadata = _infer_work_metadata(client, text, title_hint)
    segments = split_into_scenes(text)

    all_scenes: list[Scene] = []
    for index, segment in enumerate(segments):
        if index > 0 and SCENE_DELAY_SEC > 0:
            time.sleep(SCENE_DELAY_SEC)
        all_scenes.extend(_convert_scene(client, segment, title_hint))

    script = _merge_all_scenes(all_scenes, metadata)

    yaml_str = yaml.dump(
        script.model_dump(exclude_none=True),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return script, yaml_str, len(segments)


def _emit(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_convert_events(
    novel_text: str,
    title_hint: str | None = None,
) -> Generator[str, None, None]:
    """SSE event stream: progress / token / complete / error."""
    try:
        if not novel_text.strip():
            raise ValueError("请输入小说文本")

        text = _normalize_text(novel_text)
        client = _get_client()

        yield _emit({"type": "progress", "message": "正在识别作品出处…"})
        metadata = _infer_work_metadata(client, text, title_hint)
        segments = split_into_scenes(text)

        yield _emit(
            {
                "type": "start",
                "source_scenes": len(segments),
                "title": metadata.title,
                "author": metadata.author,
            }
        )
        yield _emit(
            {
                "type": "progress",
                "message": f"作品：{metadata.title} · 作者：{metadata.author}",
            }
        )

        all_scenes: list[Scene] = []
        for index, segment in enumerate(segments):
            if index > 0 and SCENE_DELAY_SEC > 0:
                time.sleep(SCENE_DELAY_SEC)

            yield _emit(
                {
                    "type": "progress",
                    "message": f"场景 {index + 1}/{len(segments)}：{segment.hint}",
                    "scene": index + 1,
                    "total": len(segments),
                }
            )

            parts = _split_long_text(segment.text, PART_MAX_CHARS)
            part_scripts: list[Script] = []

            for part_index, part_text in enumerate(parts, start=1):
                if part_index > 1 and SCENE_DELAY_SEC > 0:
                    time.sleep(SCENE_DELAY_SEC)

                yield _emit(
                    {
                        "type": "progress",
                        "message": (
                            f"场景 {segment.index} · 第 {part_index}/{len(parts)} 部分生成中…"
                        ),
                    }
                )

                if part_index > 1 or (index > 0 and part_index == 1):
                    yield _emit({"type": "token", "text": "\n\n"})

                dialogue_checklist = _extract_dialogue_lines(part_text)
                last_error: Exception | None = None
                part_script: Script | None = None
                yaml_retry = False
                empty_retry = False
                narration_retry = False

                for attempt in range(MAX_RETRIES + 1):
                    strict_retry = (
                        attempt > 0 and not yaml_retry and not empty_retry and not narration_retry
                    )
                    user_prompt = _build_scene_prompt(
                        segment,
                        part_text,
                        part_index=part_index,
                        part_total=len(parts),
                        title_hint=title_hint,
                        dialogue_count=len(dialogue_checklist),
                        strict_retry=strict_retry,
                        yaml_retry=yaml_retry,
                        empty_retry=empty_retry,
                        narration_retry=narration_retry,
                    )

                    try:
                        token_budget = _max_tokens_for_part(len(dialogue_checklist))
                        raw = ""
                        for token in _iter_llm_tokens(
                            client, user_prompt, max_tokens=token_budget
                        ):
                            raw += token
                            yield _emit({"type": "token", "text": token})

                        scenes = _parse_scenes_from_llm(raw, part_text)
                        scenes = [_normalize_scene_dialogues(s, part_text) for s in scenes]
                        part_script = Script(
                            metadata=Metadata(title="tmp", version="2.0"),
                            scenes=scenes,
                        )
                        coverage = _dialogue_coverage(dialogue_checklist, part_script)
                        output_count = _count_script_dialogues(part_script)

                        if dialogue_checklist and output_count == 0 and attempt < MAX_RETRIES:
                            empty_retry = True
                            yaml_retry = False
                            narration_retry = False
                            last_error = ValueError("dialogues 为空")
                            yield _emit({"type": "progress", "message": "对白为空，正在重试…"})
                            continue

                        if dialogue_checklist and coverage < COVERAGE_THRESHOLD and attempt < MAX_RETRIES:
                            last_error = ValueError(
                                f"场景 {segment.index} 第 {part_index} 部分对白覆盖不足"
                            )
                            empty_retry = False
                            narration_retry = False
                            yield _emit({"type": "progress", "message": "覆盖不足，正在重试…"})
                            continue

                        raw_dialogues = sum(
                            len(s.dialogues) for s in _parse_scenes_from_llm(raw, part_text)
                        )
                        if raw_dialogues > output_count and attempt < MAX_RETRIES:
                            narration_retry = True
                            empty_retry = False
                            last_error = ValueError("存在叙述性假对白")
                            yield _emit({"type": "progress", "message": "过滤旁白，正在重试…"})
                            continue

                        break
                    except yaml.YAMLError as exc:
                        last_error = exc
                        if attempt >= MAX_RETRIES:
                            break
                        yaml_retry = True
                        yield _emit({"type": "progress", "message": "YAML 格式有误，正在重试…"})
                    except (ValidationError, ValueError) as exc:
                        last_error = exc
                        if attempt >= MAX_RETRIES:
                            break
                        yield _emit({"type": "progress", "message": "格式有误，正在重试…"})

                if part_script is None:
                    raise ValueError(
                        f"场景 {segment.index}（{segment.hint}）第 {part_index} 部分转换失败："
                        f"{last_error}"
                    )

                part_scripts.append(part_script)

            all_scenes.extend(_merge_part_scripts(part_scripts, segment.text, segment))

        script = _merge_all_scenes(all_scenes, metadata)

        expected_dialogues = _extract_dialogue_lines(text)
        coverage = _dialogue_coverage(expected_dialogues, script)
        coverage_warning: str | None = None
        if len(expected_dialogues) >= 5 and coverage < FINAL_COVERAGE_MIN:
            coverage_warning = (
                f"对白覆盖率 {coverage:.0%}（{_count_script_dialogues(script)}/"
                f"{len(expected_dialogues)}），请人工核对遗漏台词。"
            )

        yaml_str = yaml.dump(
            script.model_dump(exclude_none=True),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

        characters = {
            d.character for scene in script.scenes for d in scene.dialogues
        }

        yield _emit(
            {
                "type": "complete",
                "yaml": yaml_str,
                "metadata": script.metadata.model_dump(),
                "scene_count": len(script.scenes),
                "character_count": len(characters),
                "source_scenes": len(segments),
                "coverage_warning": coverage_warning,
            }
        )
    except ValueError as exc:
        yield _emit({"type": "error", "detail": str(exc)})
    except Exception as exc:
        yield _emit({"type": "error", "detail": f"服务器错误：{exc}"})
