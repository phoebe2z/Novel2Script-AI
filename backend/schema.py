"""Pydantic models for the Novel2Script YAML schema v2."""

from datetime import date

from pydantic import BaseModel, Field, model_validator


class Metadata(BaseModel):
    title: str = Field(..., description="Script title")
    author: str = Field(default="佚名", description="Author name")
    version: str = Field(default="2.0", description="Schema version")
    created_at: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="Creation date (YYYY-MM-DD)",
    )


class Dialogue(BaseModel):
    character: str = Field(..., description="Character name")
    emotion: str | None = Field(default=None, description="Emotion tag, e.g. angry, cold")
    text: str = Field(..., description="Dialogue line")


class Scene(BaseModel):
    id: int = Field(default=1, description="Scene number (renumbered on merge)")
    slug: str = Field(..., description="Scene heading, e.g. INT. STUDY - DAY")
    action: str = Field(default="", description="Action and environment description")
    dialogues: list[Dialogue] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Production notes / transitions")

    @model_validator(mode="before")
    @classmethod
    def normalize_scene_data(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        item = dict(data)
        dialogues = item.get("dialogues")

        if dialogues is None or dialogues == "null":
            item["dialogues"] = []
        elif isinstance(dialogues, dict):
            item["dialogues"] = [dialogues]
        elif isinstance(dialogues, list):
            normalized: list[dict] = []
            for entry in dialogues:
                if isinstance(entry, str):
                    text = entry.strip().strip('"\'\u201c\u201d')
                    if text and len(text) <= 80:
                        normalized.append({"character": "未知", "text": text})
                elif isinstance(entry, dict) and entry.get("character") and entry.get("text"):
                    normalized.append(entry)
            item["dialogues"] = normalized

        if item.get("action") is None:
            item["action"] = ""

        if item.get("id") is None:
            item["id"] = 1

        return item


class Script(BaseModel):
    metadata: Metadata
    scenes: list[Scene]
