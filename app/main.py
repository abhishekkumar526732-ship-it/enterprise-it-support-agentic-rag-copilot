"""
Enterprise IT Support Agentic RAG Copilot — Main Application
─────────────────────────────────────────────────────────────
Startup sequence:
  1. Configure logging
  2. Initialise audit DB
  3. Initialise AI Observability (LangSmith)
  4. Mount API routes (chat, auth, evaluation)
  5. Mount static files & templates
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as chat_router
from app.api.auth import router as auth_router
from app.api.evaluation import router as eval_router
from app.core.config import get_settings, BASE_DIR
from app.core.logging import configure_logging
from app.core.observability import init_observability
from app.services.audit import init_db


# ───────────────────────────── Bootstrap ─────────────────────────
configure_logging()
settings = get_settings()
init_db()
init_observability()

# ───────────────────────────── FastAPI App ───────────────────────
app = FastAPI(
    title=settings.app_name,
    version="2.2.0",
    description="Enterprise IT Support Copilot with Agentic RAG, LangSmith Observability, Redis Caching, LangMem Long-Term Memory, PostgreSQL Persistence, NeMo Guardrails, LiteLLM Gateway, JWT/OIDC RBAC, and Ragas Evaluation.",
)

# ── CORS (adjust origins for production) ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────
app.include_router(auth_router)      # /api/auth/*
app.include_router(chat_router)      # /api/chat, /api/ingest, /api/health
app.include_router(eval_router)      # /api/evaluate/*

# ── Static files & templates ────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.app_name})
