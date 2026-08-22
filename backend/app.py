"""
app.py — FastAPI приложение Presentator.

Эндпоинты:
  POST /api/generate         — запускает генерацию, стримит прогресс через SSE
  GET  /api/presentations/{id}          — HTML-превью готовой презентации
  GET  /api/presentations/{id}/pptx     — скачать .pptx
  GET  /api/health                      — healthcheck
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from nanoid import generate as nanoid_generate
from pydantic import BaseModel, Field, field_validator

from generators import (
    GenerationError,
    LAYOUTS_WITH_IMAGE,
    assign_layouts,
    generate_layout_content,
    generate_outline,
)
from image_fetcher import fetch_image_url
from pptx_builder import build_pptx
from slidev_builder import build_html_preview

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("slideforge.app")

app = FastAPI(title="Presentator API", version="1.0.0")

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory хранилище готовых презентаций: { id: {html, pptx_bytes, title, created_at} }
# Для продакшна с несколькими воркерами замени на Redis/БД.
PRESENTATIONS: Dict[str, dict] = {}

VALID_STYLES = {"minimal", "academic", "creative", "corporate", "dark"}


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300)
    slide_count: int = Field(10, ge=5, le=25)
    style: str = Field("minimal")
    language: str = Field("ru")
    with_notes: bool = Field(False)
    detailed_prompt: Optional[str] = Field(None, max_length=1000)

    @field_validator("style")
    @classmethod
    def validate_style(cls, v: str) -> str:
        if v not in VALID_STYLES:
            raise ValueError(f"style должен быть одним из {VALID_STYLES}")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in {"ru", "en"}:
            raise ValueError("language должен быть 'ru' или 'en'")
        return v


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _generation_stream(req: GenerateRequest):
    started = time.time()
    try:
        yield sse_event("status", {"stage": "outline", "message": "Генерируем структуру презентации..."})
        outline = await generate_outline(
            req.topic, req.slide_count, req.language, req.detailed_prompt
        )
        yield sse_event(
            "outline",
            {"slides": [{"title": o.title, "key_points": o.key_points} for o in outline]},
        )

        layouts = assign_layouts(req.slide_count)

        slides_data = []
        for i, item in enumerate(outline):
            layout = layouts[i]
            yield sse_event(
                "status",
                {
                    "stage": "content",
                    "message": f"Наполняем слайд {i + 1} из {len(outline)}...",
                    "progress": round(((i) / len(outline)) * 70 + 10, 1),
                },
            )
            content = await generate_layout_content(
                req.topic, item, layout, req.style, req.language, req.with_notes, req.detailed_prompt
            )

            image_url = None
            if layout in LAYOUTS_WITH_IMAGE:
                image_url = await fetch_image_url(content.get("image_keywords") or [])

            slide_dict = dict(content)
            slide_dict["image_url"] = image_url
            slide_dict["notes"] = content.get("speaker_notes")
            slides_data.append(slide_dict)

            yield sse_event(
                "slide",
                {
                    "index": i,
                    "layout": layout,
                    "title": content.get("title"),
                    "image_url": image_url,
                },
            )

        yield sse_event(
            "status", {"stage": "assembling", "message": "Собираем презентацию...", "progress": 85}
        )

        html_preview = build_html_preview(req.topic, slides_data, req.style, req.language)
        pptx_bytes = await build_pptx(req.topic, slides_data, req.style, req.language)

        presentation_id = nanoid_generate(size=10)
        PRESENTATIONS[presentation_id] = {
            "html": html_preview,
            "pptx": pptx_bytes,
            "title": req.topic,
            "created_at": time.time(),
        }

        elapsed = round(time.time() - started, 1)
        yield sse_event(
            "done",
            {
                "id": presentation_id,
                "preview_url": f"/api/presentations/{presentation_id}",
                "pptx_url": f"/api/presentations/{presentation_id}/pptx",
                "elapsed_seconds": elapsed,
                "slide_count": len(slides_data),
            },
        )

    except GenerationError as exc:
        logger.warning("Ошибка генерации: %s", exc)
        yield sse_event("error", {"message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Непредвиденная ошибка генерации")
        yield sse_event(
            "error",
            {"message": "Произошла непредвиденная ошибка на сервере. Попробуй ещё раз."},
        )


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    return StreamingResponse(
        _generation_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/presentations/{presentation_id}", response_class=HTMLResponse)
async def get_presentation_html(presentation_id: str):
    entry = PRESENTATIONS.get(presentation_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Презентация не найдена или истекло время хранения.")
    return HTMLResponse(content=entry["html"])


@app.get("/api/presentations/{presentation_id}/pptx")
async def get_presentation_pptx(presentation_id: str):
    entry = PRESENTATIONS.get(presentation_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Презентация не найдена или истекло время хранения.")
    filename = "".join(c for c in entry["title"][:40] if c.isalnum() or c in " -_").strip() or "presentation"
    return Response(
        content=entry["pptx"],
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pptx"'},
    )


@app.get("/api/health")
async def health():
    return JSONResponse({"status": "ok", "presentations_in_memory": len(PRESENTATIONS)})


# Простая периодическая очистка старых презентаций (старше 6 часов), чтобы не течь по памяти
@app.on_event("startup")
async def start_cleanup_task():
    async def cleanup():
        while True:
            await asyncio.sleep(3600)
            cutoff = time.time() - 6 * 3600
            expired = [pid for pid, v in PRESENTATIONS.items() if v["created_at"] < cutoff]
            for pid in expired:
                PRESENTATIONS.pop(pid, None)
            if expired:
                logger.info("Очищено %d устаревших презентаций", len(expired))

    asyncio.create_task(cleanup())
