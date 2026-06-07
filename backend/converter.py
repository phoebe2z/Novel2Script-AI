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


def _parse_scenes_from_llm(raw: str) -> list[Scene]:
    """Parse LLM output; accepts full script or scenes-only YAML."""
    data = _parse_yaml(raw)
    scenes_data = data.get("scenes")

    if scenes_data is None:
        raise ValueError("AI 输出缺少 scenes 列表")

    if isinstance(scenes_data, dict):
        scenes_data = [scenes_data]
    elif not isinstance(scenes_data, list):
        raise ValueError("scenes 必须是列表")

    return [Scene.model_validate(item) for item in scenes_data]


def _merge_scenes_into_one(scenes: list[Scene]) -> Scene:
    """将同一场戏的多段输出合并为一个 Scene。"""
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


def _infer_work_metadata(
    client: OpenAI,
    novel_text: str,
    title_hint: str | None,
) -> Metadata:
    """Identify source work title and author from novel excerpt."""
    if title_hint:
        return Metadata(title=title_hint, author="佚名", version="2.0")

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
            if re.match(r"^场景\s*\d", title) or title in {"开场", "未命名"}:
                title = _fallback_title_from_text(novel_text)
            return Metadata(title=title, author=author, version="2.0")
    except Exception:
        pass

    return Metadata(title=_fallback_title_from_text(novel_text), author="佚名", version="2.0")


def _fallback_title_from_text(text: str) -> str:
    """Heuristic fallback when LLM inference fails."""
    known = [
        ("汪淼", "三体"),
        ("罗辑", "三体"),
        ("哈利", "哈利·波特"),
        ("贾宝玉", "红楼梦"),
        ("孙悟空", "西游记"),
    ]
    for keyword, title in known:
        if keyword in text:
            return title
    return "未命名"


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
        "【只输出 scenes 列表（以 scenes: 开头），不要输出 metadata】",
        "【只输出 1 个 scene 对象；id 固定写 1（系统会重新编号）】",
        "【dialogues 必须是列表 []，禁止 null；无对白时写 dialogues: []】",
    ]

    if title_hint and segment.index == 1 and part_index == 1:
        parts.append(f"出处作品提示：{title_hint}（metadata 由系统写入，此处勿生成 title）")

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
            scenes = _parse_scenes_from_llm(raw)
            script = Script(metadata=Metadata(title="tmp", version="2.0"), scenes=scenes)
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

    return _merge_part_scripts(part_scripts)


def _merge_part_scripts(scripts: list[Script]) -> list[Scene]:
    """将同一场戏的多部分输出合并为一个 Scene。"""
    if not scripts:
        raise ValueError("无有效场景输出")

    scenes: list[Scene] = []
    for script in scripts:
        scenes.extend(script.scenes)

    return [_merge_scenes_into_one(scenes)]


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
    先按场景切分原文，再逐场景生成剧本，最后合并。
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

                        part_script = Script(
                            metadata=Metadata(title="tmp", version="2.0"),
                            scenes=_parse_scenes_from_llm(raw),
                        )
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

            all_scenes.extend(_merge_part_scripts(part_scripts))

        script = _merge_all_scenes(all_scenes, metadata)

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
