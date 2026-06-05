"""Rule-based scene segmentation for Chinese fiction."""

import re
from dataclasses import dataclass

# 在新场景文字之前切分（按优先级排列）
_SCENE_BREAK_PATTERNS: list[tuple[str, str]] = [
    (r"接汪淼的汽车|接.+?的汽车驶进|汽车驶进了|汽车驶进", "转场-乘车"),
    (r"会议是在一个大厅里|会议是在|大厅里举行", "作战中心会议"),
    (r"作战中心", "作战中心"),
    (r"那是一年前|一年前，汪淼", "闪回"),
    (r"走进了?.+?(?:大院|大厅|房间|楼道|工地|实验室)", "进入场所"),
    (r"第二天|次日|当晚|翌日|清晨|黄昏|夜幕降临", "时间跳转"),
]

_MIN_SCENE_CHARS = 80


@dataclass
class SceneSegment:
    index: int
    text: str
    hint: str


def _find_break_positions(text: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = [(0, "开场")]

    for pattern, hint in _SCENE_BREAK_PATTERNS:
        for match in re.finditer(pattern, text):
            pos = match.start()
            if pos > 0:
                candidates.append((pos, hint))

    candidates.sort(key=lambda item: item[0])

    deduped: list[tuple[int, str]] = []
    seen: set[int] = set()
    for pos, hint in candidates:
        if pos in seen:
            continue
        seen.add(pos)
        deduped.append((pos, hint))

    # 过滤距离过近的切分点
    filtered: list[tuple[int, str]] = []
    for pos, hint in deduped:
        if not filtered or pos - filtered[-1][0] >= _MIN_SCENE_CHARS:
            filtered.append((pos, hint))

    return filtered


def split_into_scenes(text: str) -> list[SceneSegment]:
    """将小说文本按叙事场景切分。"""
    text = text.strip()
    if not text:
        return []

    breaks = _find_break_positions(text)

    # 长文但无明显场景标记时，按句号切段
    if len(breaks) <= 1 and len(text) > 1500:
        return _split_by_length(text, max_chars=1000)

    segments: list[SceneSegment] = []
    for index, (start, hint) in enumerate(breaks):
        end = breaks[index + 1][0] if index + 1 < len(breaks) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            segments.append(SceneSegment(index=len(segments) + 1, text=chunk, hint=hint))

    return segments or [SceneSegment(index=1, text=text, hint="全场")]


def _split_by_length(text: str, max_chars: int = 1000) -> list[SceneSegment]:
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
