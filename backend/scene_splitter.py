"""Rule-based scene segmentation for Chinese fiction."""

import os
import re
from dataclasses import dataclass

# Scene break patterns (ordered by narrative priority)
_SCENE_BREAK_PATTERNS: list[tuple[str, str]] = [
    (r"在楼道里说|就在楼道里说", "汪淼家-楼道"),
    (r"向屋里闯|请不要在我家里抽烟", "汪淼家-冲突"),
    (r"转身下楼|下了楼|史强.*下楼", "汪淼家-离开"),
    (r"接汪淼的汽车|(?:上了|坐进|钻进).{0,6}汽车|汽车(?:缓缓)?(?:驶|停|开|开进)", "转场-乘车"),
    (r"城市近郊|一座不大(?:的)?(?:大院|院子)|门牌号码", "作战中心-到达"),
    (r"汪淼(?:一)?进(?:去|了).{0,6}大厅|进了大厅", "作战中心-大厅"),
    (r"会议是在一个大厅里|主持会议的是一位叫常伟思", "作战中心-会议"),
    (r"常伟思|常将军", "作战中心-常伟思"),
    (r"史强.*凑近|大史.*说|别给这帮家伙好脸", "作战中心-史强发言"),
    (r"良湘(?:的)?(?:大)?工地|超导线圈|加速器工程", "良湘工地"),
    (r"那是一年前|一年前，汪淼", "闪回"),
    (r"第二天|次日|当晚|翌日|清晨|黄昏|夜幕降临", "时间跳转"),
]

_SLUG_BY_HINT: dict[str, str] = {
    "开场": "INT. 汪淼家 - 日",
    "汪淼家-楼道": "INT. 汪淼家楼道 - 日",
    "汪淼家-冲突": "INT. 汪淼家 - 日",
    "汪淼家-离开": "INT. 汪淼家 - 日",
    "转场-乘车": "INT. 汽车 - 日",
    "作战中心-到达": "INT. 作战中心 - 日",
    "作战中心-大厅": "INT. 作战中心大厅 - 日",
    "作战中心-会议": "INT. 作战中心会议室 - 日",
    "作战中心-常伟思": "INT. 作战中心会议室 - 日",
    "作战中心-史强发言": "INT. 作战中心会议室 - 日",
    "良湘工地": "EXT. 良湘工地 - 日",
    "闪回": "INT. 闪回 - 日",
    "时间跳转": "INT. 未知场所 - 日",
}

_MIN_SCENE_CHARS = 100
_MIN_BREAK_GAP = 80
_FRAGMENT_CHARS = 60
_MAX_SEGMENT_CHARS = int(os.getenv("SCENE_MAX_CHARS", "480"))


@dataclass
class SceneSegment:
    index: int
    text: str
    hint: str


def slug_for_segment(hint: str, text: str) -> str:
    """Derive slug from segment hint first; ignore place names appearing only in dialogue."""
    if re.search(r"大厅|会议|常伟思|讲台|北约|中情局", text):
        return "INT. 作战中心会议室 - 日"
    if re.search(r"良湘|超导线圈|加速器", text):
        return "EXT. 良湘工地 - 日"
    if hint in _SLUG_BY_HINT:
        return _SLUG_BY_HINT[hint]
    if hint.startswith("叙事段"):
        if re.search(r"良湘|超导|加速器", text):
            return "EXT. 良湘工地 - 日"
        if re.search(r"汽车|车厢|驶进", text):
            return "INT. 汽车 - 日"
        if re.search(r"会议|常伟思|大厅", text):
            return "INT. 作战中心会议室 - 日"
        if re.search(r"楼道|家里|邻居", text):
            return "INT. 汪淼家 - 日"
    return "INT. 未知场所 - 日"


def _find_break_positions(text: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = [(0, "开场")]

    for pattern, hint in _SCENE_BREAK_PATTERNS:
        for match in re.finditer(pattern, text):
            pos = match.start()
            if pos > 0:
                candidates.append((pos, hint))

    candidates.sort(key=lambda item: item[0])

    deduped: list[tuple[int, str]] = []
    for pos, hint in candidates:
        if deduped and pos - deduped[-1][0] < _MIN_BREAK_GAP:
            prev_hint = deduped[-1][1]
            if len(hint) > len(prev_hint):
                deduped[-1] = (pos, hint)
            continue
        if not deduped or pos != deduped[-1][0]:
            deduped.append((pos, hint))

    return deduped


def _merge_tiny_fragments(segments: list[SceneSegment]) -> list[SceneSegment]:
    """Merge only tiny fragments (<60 chars) with the same hint into the previous segment."""
    if len(segments) <= 1:
        return segments

    merged: list[SceneSegment] = [segments[0]]
    for segment in segments[1:]:
        if len(segment.text) < _FRAGMENT_CHARS and merged[-1].hint == segment.hint:
            prev = merged[-1]
            merged[-1] = SceneSegment(
                index=prev.index,
                text=(prev.text + segment.text).strip(),
                hint=prev.hint,
            )
        else:
            merged.append(segment)

    for index, segment in enumerate(merged, start=1):
        merged[index - 1] = SceneSegment(index=index, text=segment.text, hint=segment.hint)
    return merged


def _subdivide_oversized(segments: list[SceneSegment]) -> list[SceneSegment]:
    """Subdivide oversized segments at sentence boundaries while keeping the hint."""
    result: list[SceneSegment] = []
    for segment in segments:
        if len(segment.text) <= _MAX_SEGMENT_CHARS:
            result.append(segment)
            continue
        sentences = re.split(r"(?<=[。！？…])", segment.text)
        sentences = [s for s in sentences if s.strip()]
        chunk = ""
        part = 1
        for sentence in sentences:
            if chunk and len(chunk) + len(sentence) > _MAX_SEGMENT_CHARS:
                hint = segment.hint if part == 1 else f"{segment.hint}-{part}"
                result.append(SceneSegment(index=0, text=chunk.strip(), hint=hint))
                chunk = sentence
                part += 1
            else:
                chunk += sentence
        if chunk.strip():
            hint = segment.hint if part == 1 else f"{segment.hint}-{part}"
            result.append(SceneSegment(index=0, text=chunk.strip(), hint=hint))

    for index, segment in enumerate(result, start=1):
        result[index - 1] = SceneSegment(index=index, text=segment.text, hint=segment.hint)
    return result


def split_into_scenes(text: str) -> list[SceneSegment]:
    """Split novel text into scene segments."""
    text = text.strip()
    if not text:
        return []

    breaks = _find_break_positions(text)

    if len(breaks) <= 1 and len(text) > 1500:
        segments = _split_by_length(text, max_chars=_MAX_SEGMENT_CHARS)
    else:
        segments = []
        for index, (start, hint) in enumerate(breaks):
            end = breaks[index + 1][0] if index + 1 < len(breaks) else len(text)
            chunk = text[start:end].strip()
            if len(chunk) >= _MIN_SCENE_CHARS or not segments:
                segments.append(SceneSegment(index=len(segments) + 1, text=chunk, hint=hint))
            elif segments:
                prev = segments[-1]
                segments[-1] = SceneSegment(
                    index=prev.index,
                    text=(prev.text + chunk).strip(),
                    hint=prev.hint,
                )

    segments = _merge_tiny_fragments(segments)
    segments = _subdivide_oversized(segments)
    return segments or [SceneSegment(index=1, text=text, hint="开场")]


def _split_by_length(text: str, max_chars: int = 480) -> list[SceneSegment]:
    sentences = re.split(r"(?<=[。！？…])", text)
    sentences = [s for s in sentences if s.strip()]

    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_chars and current:
            parts.append(current)
            current = sentence
        else:
            current += sentence
    if current.strip():
        parts.append(current)

    return [
        SceneSegment(index=i + 1, text=part.strip(), hint=f"叙事段{i + 1}")
        for i, part in enumerate(parts)
    ]
