"""Novel-to-script conversion via LLM."""

import os
import re

import yaml
from openai import OpenAI
from pydantic import ValidationError

from prompts import SYSTEM_PROMPT
from schema import Script

MAX_RETRIES = 2


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未设置 OPENAI_API_KEY 环境变量")
    base_url = os.getenv("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences if the model wraps YAML in them."""
    text = text.strip()
    match = re.match(r"^```(?:ya?ml)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _parse_yaml(raw: str) -> dict:
    cleaned = _strip_code_fence(raw)
    data = yaml.safe_load(cleaned)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是有效的 YAML 对象")
    return data


def _call_llm(client: OpenAI, novel_text: str, title_hint: str | None) -> str:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_content = novel_text
    if title_hint:
        user_content = f"标题提示：{title_hint}\n\n{novel_text}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def convert_novel_to_script(
    novel_text: str,
    title_hint: str | None = None,
) -> tuple[Script, str]:
    """
    Convert novel text to a validated Script model and YAML string.

    Returns (script, yaml_string). Raises on failure after retries.
    """
    if not novel_text.strip():
        raise ValueError("请输入小说文本")

    client = _get_client()
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            raw = _call_llm(client, novel_text, title_hint)
            data = _parse_yaml(raw)
            script = Script.model_validate(data)
            yaml_str = yaml.dump(
                script.model_dump(exclude_none=True),
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            return script, yaml_str
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                repair_prompt = (
                    f"上次输出格式有误：{exc}\n"
                    "请重新输出符合 Schema 的纯 YAML，不要包含任何额外说明。"
                )
                novel_text = repair_prompt + "\n\n原文：\n" + novel_text
            continue

    raise ValueError(f"转换失败（已重试 {MAX_RETRIES} 次）：{last_error}")
