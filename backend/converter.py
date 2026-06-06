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

from prompts import COMPLETENESS_REMINDER, SYSTEM_PROMPT
from scene_splitter import SceneSegment, split_into_scenes
from schema import Metadata, Scene, Script

MAX_RETRIES = 2
# 单场戏内部若仍过长，再按字数切小片
PART_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "500"))
COVERAGE_THRESHOLD = float(os.getenv("DIALOGUE_COVERAGE_THRESHOLD", "0.8"))
SCENE_DELAY_SEC = float(os.getenv("CHUNK_DELAY_SEC", "2"))

_DIALOGUE_PATTERN = re.compile(
    r'["\u201c\u201d\u300c\u300d]([^"\u201c\u201d\u300c\u300d]+?)["\u201c\u201d\u300c\u300d]'
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


def _count_script_dialogues(script: Script) -> int:
    return sum(len(scene.dialogues) for scene in script.scenes)


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            parts.append(current)
            current = sentence
    if current:
        parts.append(current)

    return parts


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


def _build_scene_prompt(
    segment: SceneSegment,
    part_text: str,
    *,
    part_index: int,
    part_total: int,
    title_hint: str | None,
    dialogue_count: int,
    strict_retry: bool,
) -> str:
    parts = [
        f"【场景 {segment.index}：{segment.hint}】",
        f"【本场第 {part_index}/{part_total} 部分，同一场戏请保持 slug 一致】",
    ]

    if title_hint and segment.index == 1 and part_index == 1:
        parts.append(f"标题提示：{title_hint}")

    if dialogue_count:
        parts.append(
            f"【本部分含 {dialogue_count} 句引号对白，必须全部写入 dialogues，按顺序，不得遗漏】"
        )

    if strict_retry:
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


def _iter_llm_tokens(client: OpenAI, user_content: str) -> Iterator[str]:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.05,
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096")),
            stream=True,
        )
    except Exception as exc:
        _handle_llm_error(exc)
        return

    for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        if token:
            yield token


def _call_llm(client: OpenAI, user_content: str) -> str:
    return "".join(_iter_llm_tokens(client, user_content))


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

    for attempt in range(MAX_RETRIES + 1):
        strict_retry = attempt > 0
        user_prompt = _build_scene_prompt(
            segment,
            part_text,
            part_index=part_index,
            part_total=part_total,
            title_hint=title_hint,
            dialogue_count=len(dialogue_checklist),
            strict_retry=strict_retry,
        )

        try:
            raw = _call_llm(client, user_prompt)
            script = Script.model_validate(_parse_yaml(raw))
            coverage = _dialogue_coverage(dialogue_checklist, script)

            if coverage < COVERAGE_THRESHOLD and attempt < MAX_RETRIES:
                last_error = ValueError(
                    f"场景 {segment.index} 第 {part_index} 部分对白覆盖不足："
                    f"{int(coverage * len(dialogue_checklist))}/{len(dialogue_checklist)}"
                )
                continue

            return script
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
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
) -> Script:
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

    return _merge_part_scripts(part_scripts)


def _merge_part_scripts(scripts: list[Script]) -> Script:
    """将同一场戏的多部分输出合并为一个 Script。"""
    if not scripts:
        raise ValueError("无有效场景输出")

    title = scripts[0].metadata.title
    scenes: list[Scene] = []
    for script in scripts:
        scenes.extend(script.scenes)

    return Script(
        metadata=Metadata(title=title, version="2.0"),
        scenes=scenes,
    )


def _merge_all_scenes(scene_scripts: list[Script], title: str) -> Script:
    merged_scenes: list[Scene] = []
    scene_id = 1

    for script in scene_scripts:
        for scene in script.scenes:
            merged_scenes.append(scene.model_copy(update={"id": scene_id}))
            scene_id += 1

    return Script(metadata=Metadata(title=title, version="2.0"), scenes=merged_scenes)


def _resolve_title(scene_scripts: list[Script], title_hint: str | None) -> str:
    if title_hint:
        return title_hint
    for script in scene_scripts:
        if script.metadata.title:
            return script.metadata.title
    return "未命名"


def convert_novel_to_script(
    novel_text: str,
    title_hint: str | None = None,
) -> tuple[Script, str, int]:
    """
    先按场景切分原文，再逐场景生成剧本，最后合并。
    """
    if not novel_text.strip():
        raise ValueError("请输入小说文本")

    text = _normalize_text(novel_text)
    client = _get_client()
    segments = split_into_scenes(text)

    scene_scripts: list[Script] = []
    for index, segment in enumerate(segments):
        if index > 0 and SCENE_DELAY_SEC > 0:
            time.sleep(SCENE_DELAY_SEC)
        scene_scripts.append(_convert_scene(client, segment, title_hint))

    title = _resolve_title(scene_scripts, title_hint)
    script = _merge_all_scenes(scene_scripts, title)

    expected_dialogues = _extract_dialogue_lines(text)
    coverage = _dialogue_coverage(expected_dialogues, script)
    if len(expected_dialogues) >= 5 and coverage < 0.75:
        raise ValueError(
            f"对白覆盖仍不足：输出 {_count_script_dialogues(script)} 句，"
            f"原文约 {len(expected_dialogues)} 句（覆盖率 {coverage:.0%}）。"
            f"已自动切为 {len(segments)} 个场景，可尝试缩短输入或调低 CHUNK_MAX_CHARS。"
        )

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
        segments = split_into_scenes(text)

        yield _emit({"type": "start", "source_scenes": len(segments)})

        scene_scripts: list[Script] = []
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

                for attempt in range(MAX_RETRIES + 1):
                    strict_retry = attempt > 0
                    user_prompt = _build_scene_prompt(
                        segment,
                        part_text,
                        part_index=part_index,
                        part_total=len(parts),
                        title_hint=title_hint,
                        dialogue_count=len(dialogue_checklist),
                        strict_retry=strict_retry,
                    )

                    try:
                        raw = ""
                        for token in _iter_llm_tokens(client, user_prompt):
                            raw += token
                            yield _emit({"type": "token", "text": token})

                        part_script = Script.model_validate(_parse_yaml(raw))
                        coverage = _dialogue_coverage(dialogue_checklist, part_script)

                        if coverage < COVERAGE_THRESHOLD and attempt < MAX_RETRIES:
                            last_error = ValueError(
                                f"场景 {segment.index} 第 {part_index} 部分对白覆盖不足"
                            )
                            yield _emit({"type": "progress", "message": "覆盖不足，正在重试…"})
                            continue

                        break
                    except (yaml.YAMLError, ValidationError, ValueError) as exc:
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

            scene_scripts.append(_merge_part_scripts(part_scripts))

        title = _resolve_title(scene_scripts, title_hint)
        script = _merge_all_scenes(scene_scripts, title)

        expected_dialogues = _extract_dialogue_lines(text)
        coverage = _dialogue_coverage(expected_dialogues, script)
        if len(expected_dialogues) >= 5 and coverage < 0.75:
            raise ValueError(
                f"对白覆盖仍不足：输出 {_count_script_dialogues(script)} 句，"
                f"原文约 {len(expected_dialogues)} 句（覆盖率 {coverage:.0%}）。"
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
            }
        )
    except ValueError as exc:
        yield _emit({"type": "error", "detail": str(exc)})
    except Exception as exc:
        yield _emit({"type": "error", "detail": f"服务器错误：{exc}"})
