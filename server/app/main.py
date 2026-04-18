from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import cors_origin_list, settings
from app.db import connect_db, disconnect_db
from app.routers import ai_pipeline, auth, image_composition, posts, tags, uploads
from app.services.scheduler_service import publishing_scheduler
from app.services.storage_service import LOCAL_UPLOAD_ROOT

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    publishing_scheduler.start()
    try:
        yield
    finally:
        await publishing_scheduler.stop()
        await disconnect_db()


app = FastAPI(
    title="AI Social Media Manager API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    try:
        request_size = int(content_length) if content_length else 0
    except ValueError:
        request_size = 0

    if request_size > settings.max_request_body_bytes:
        return JSONResponse(
            status_code=413,
            content={
                "detail": f"Request body exceeds {settings.max_request_body_bytes} bytes.",
            },
        )
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled request error")
    response = JSONResponse(status_code=500, content={"detail": str(exc) or "Internal server error."})
    origin = request.headers.get("origin")
    allowed_origins = cors_origin_list()
    if origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCAL_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(LOCAL_UPLOAD_ROOT)), name="local-uploads")

app.include_router(uploads.router)
app.include_router(auth.router)
app.include_router(ai_pipeline.router)
app.include_router(image_composition.router)
app.include_router(posts.router)
app.include_router(tags.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AI Social Media Manager API"}
