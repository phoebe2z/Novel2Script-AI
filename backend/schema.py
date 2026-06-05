"""Pydantic models for the Novel2Script YAML schema v2."""

from datetime import date

from pydantic import BaseModel, Field


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
    id: int = Field(..., description="场景编号")
    slug: str = Field(..., description="场景标头，如 INT. 书房 - 日")
    action: str = Field(..., description="镜头动作与环境描写")
    dialogues: list[Dialogue] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="拍摄提示/转场说明")


class Script(BaseModel):
    metadata: Metadata
    scenes: list[Scene]
