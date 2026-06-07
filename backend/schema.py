"""Pydantic models for the Novel2Script YAML schema v2."""

from datetime import date

from pydantic import BaseModel, Field, model_validator


class Metadata(BaseModel):
    title: str = Field(..., description="剧本标题")
    author: str = Field(default="佚名", description="作者")
    version: str = Field(default="2.0", description="Schema 版本号")
    created_at: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="创建日期 (YYYY-MM-DD)",
    )


class Dialogue(BaseModel):
    character: str = Field(..., description="角色名")
    emotion: str | None = Field(default=None, description="情感标签，如愤怒、冷淡、讥讽")
    text: str = Field(..., description="台词内容")


class Scene(BaseModel):
    id: int = Field(default=1, description="场景编号（系统最终会重新编号）")
    slug: str = Field(..., description="场景标头，如 INT. 书房 - 日")
    action: str = Field(default="", description="镜头动作与环境描写")
    dialogues: list[Dialogue] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="拍摄提示/转场说明")

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
            item["dialogues"] = [
                d for d in dialogues
                if d is not None and isinstance(d, dict) and d.get("character") and d.get("text")
            ]

        if item.get("action") is None:
            item["action"] = ""

        if item.get("id") is None:
            item["id"] = 1

        return item


class Script(BaseModel):
    metadata: Metadata
    scenes: list[Scene]
