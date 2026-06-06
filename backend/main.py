"""FastAPI backend for Novel2Script AI."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from converter import convert_novel_to_script, stream_convert_events
from fastapi.responses import StreamingResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
load_dotenv(override=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="Novel2Script AI",
    description="小说转剧本助手 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConvertRequest(BaseModel):
    novel_text: str = Field(..., min_length=1, description="小说章节文本")
    title_hint: str | None = Field(default=None, description="可选标题提示")


class ConvertResponse(BaseModel):
    yaml: str
    metadata: dict
    scene_count: int
    character_count: int
    source_scenes: int


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/convert/stream")
async def convert_stream(request: ConvertRequest):
    return StreamingResponse(
        stream_convert_events(request.novel_text, request.title_hint),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/convert", response_model=ConvertResponse)
async def convert(request: ConvertRequest):
    try:
        script, yaml_str, source_scenes = convert_novel_to_script(
            request.novel_text,
            request.title_hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务器错误：{exc}") from exc

    characters = {
        d.character
        for scene in script.scenes
        for d in scene.dialogues
    }

    return ConvertResponse(
        yaml=yaml_str,
        metadata=script.metadata.model_dump(),
        scene_count=len(script.scenes),
        character_count=len(characters),
        source_scenes=source_scenes,
    )
