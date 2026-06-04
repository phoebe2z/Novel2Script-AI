"""Pydantic models for the Novel2Script YAML schema."""

from pydantic import BaseModel, Field


class Metadata(BaseModel):
    title: str = Field(..., description="小说标题")
    version: str = Field(default="1.0", description="Schema 版本号")


class Dialogue(BaseModel):
    character: str = Field(..., description="角色名")
    line: str = Field(..., description="台词内容")
    notes: str | None = Field(default=None, description="表演指导（可选）")


class Scene(BaseModel):
    scene_id: int = Field(..., description="场景编号")
    location: str = Field(..., description="室内/室外 - 地点 - 时间")
    action: str = Field(..., description="可被镜头记录的动作与环境描述")
    dialogues: list[Dialogue] = Field(default_factory=list)


class Script(BaseModel):
    metadata: Metadata
    script_content: list[Scene]
