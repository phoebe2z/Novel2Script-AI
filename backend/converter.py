"""Novel-to-script conversion via LLM."""

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from prompts import COMPLETENESS_REMINDER, SYSTEM_PROMPT
from schema import Metadata, Script

MAX_RETRIES = 3
CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1800"))
COVERAGE_THRESHOLD = float(os.getenv("DIALOGUE_COVERAGE_THRESHOLD", "0.9"))

# 弯引号 ""、直角「」、直引号 "
_DIALOGUE_PATTERN = re.compile(
    r'["\u201c\u201d\u300c\u300d]([^"\u201c\u201d\u300c\u300d]+?)["\u201c\u201d\u300c\u300d]'
)


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
    return re.sub(r"\s+", "", _normalize_text(text))


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:ya?ml)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _parse_yaml(raw: str) -> dict:
    cleaned = _strip_code_fence(raw)
    data = yaml.safe_load(cleaned)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是有效的 YAML 对象")
    return data


def _extract_dialogue_lines(text: str) -> list[str]:
    lines = _DIALOGUE_PATTERN.findall(text)
    return [line.strip() for line in lines if len(line.strip()) >= 2]


def _count_source_dialogues(text: str) -> int:
    return len(_extract_dialogue_lines(text))


def _count_script_dialogues(script: Script) -> int:
    return sum(len(scene.dialogues) for scene in script.script_content)


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if not paragraphs:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        extra = len(paragraph) + (2 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += extra

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _dialogue_coverage(expected: list[str], script: Script) -> float:
    if not expected:
        return 1.0

    script_lines = [
        _normalize_line(d.line)
        for scene in script.script_content
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


def _build_user_prompt(
    chunk_text: str,
    *,
    chunk_index: int,
    chunk_total: int,
    title_hint: str | None,
    dialogue_checklist: list[str],
    strict_retry: bool,
) -> str:
    parts = [f"【片段 {chunk_index + 1}/{chunk_total}】"]

    if title_hint and chunk_index == 0:
        parts.append(f"标题提示：{title_hint}")

    if dialogue_checklist:
        parts.append(
            f"【对白强制清单：共 {len(dialogue_checklist)} 句，"
            "必须全部写入 dialogues，按叙事顺序排列，line 保留原文，不得遗漏】"
        )
        for index, line in enumerate(dialogue_checklist, start=1):
            parts.append(f"{index}. {line}")

    if strict_retry:
        parts.append(COMPLETENESS_REMINDER)

    parts.extend(["【小说原文片段】", chunk_text])
    return "\n\n".join(parts)


def _call_llm(client: OpenAI, user_content: str) -> str:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.05,
        max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "16384")),
    )
    return response.choices[0].message.content or ""


def _convert_chunk(
    client: OpenAI,
    chunk_text: str,
    *,
    chunk_index: int,
    chunk_total: int,
    title_hint: str | None,
) -> Script:
    dialogue_checklist = _extract_dialogue_lines(chunk_text)
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        strict_retry = attempt > 0
        user_prompt = _build_user_prompt(
            chunk_text,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            title_hint=title_hint,
            dialogue_checklist=dialogue_checklist,
            strict_retry=strict_retry,
        )

        try:
            raw = _call_llm(client, user_prompt)
            script = Script.model_validate(_parse_yaml(raw))
            coverage = _dialogue_coverage(dialogue_checklist, script)

            if coverage < COVERAGE_THRESHOLD and attempt < MAX_RETRIES:
                last_error = ValueError(
                    f"片段 {chunk_index + 1} 对白覆盖不足："
                    f"{int(coverage * len(dialogue_checklist))}/{len(dialogue_checklist)}"
                )
                continue

            return script
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break

    raise ValueError(f"片段 {chunk_index + 1} 转换失败：{last_error}")


def _merge_scripts(scripts: list[Script], title: str) -> Script:
    scenes = []
    scene_id = 1

    for script in scripts:
        for scene in script.script_content:
            scenes.append(scene.model_copy(update={"scene_id": scene_id}))
            scene_id += 1

    return Script(metadata=Metadata(title=title, version="1.0"), script_content=scenes)


def _resolve_title(scripts: list[Script], title_hint: str | None) -> str:
    if title_hint:
        return title_hint
    for script in scripts:
        if script.metadata.title:
            return script.metadata.title
    return "未命名"


def convert_novel_to_script(
    novel_text: str,
    title_hint: str | None = None,
) -> tuple[Script, str]:
    """
    Convert novel text to a validated Script model and YAML string.

    Long texts are split into chunks with per-chunk dialogue checklists.
    """
    if not novel_text.strip():
        raise ValueError("请输入小说文本")

    text = _normalize_text(novel_text)
    client = _get_client()
    chunks = _split_into_chunks(text, CHUNK_MAX_CHARS)

    chunk_scripts = [
        _convert_chunk(
            client,
            chunk,
            chunk_index=index,
            chunk_total=len(chunks),
            title_hint=title_hint,
        )
        for index, chunk in enumerate(chunks)
    ]

    title = _resolve_title(chunk_scripts, title_hint)
    script = _merge_scripts(chunk_scripts, title)

    expected_dialogues = _extract_dialogue_lines(text)
    coverage = _dialogue_coverage(expected_dialogues, script)
    if len(expected_dialogues) >= 5 and coverage < 0.85:
        raise ValueError(
            f"对白覆盖仍不足：输出 {_count_script_dialogues(script)} 句，"
            f"原文约 {len(expected_dialogues)} 句（覆盖率 {coverage:.0%}）。"
            "建议缩短单次输入或更换更强模型后重试。"
        )

    yaml_str = yaml.dump(
        script.model_dump(exclude_none=True),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return script, yaml_str
