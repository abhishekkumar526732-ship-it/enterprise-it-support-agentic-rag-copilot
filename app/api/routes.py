"""
API Routes — JWT-protected endpoints with PostgreSQL backend
────────────────────────────────────────────────────────────
Endpoints:
  • GET  /api/health      → public status check (includes DB status)
  • POST /api/chat        → chat endpoint (user+ role, logs to PostgreSQL audit)
  • POST /api/ingest      → document ingestion (admin role, tracks in PostgreSQL)
  • GET  /api/audit       → query audit log history (analyst+ role)
  • POST /api/tickets     → create IT support ticket (user+ role)
  • GET  /api/tickets     → list IT support tickets (user sees own, analyst/admin sees all)
"""

import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import TokenPayload, require_user, require_admin, require_analyst
from app.core.redis_client import is_redis_available
from app.core.observability import is_langsmith_active
from app.db.session import is_postgres_available, get_session_factory
from app.db.models import AuditLog, ITSupportTicket, KnowledgeDoc
from app.rag.workflow import ask
from app.rag.vectorstore import add_documents
from app.services.ingestion import load_file, chunk_documents, upload_to_s3_if_enabled, SUPPORTED
from app.services.audit import write_audit
from app.services.cache import get_cached_response, set_cached_response
from app.services.memory import get_user_memories, clear_user_memories

router = APIRouter(prefix="/api")
settings = get_settings()


# ───────────────────────────── Pydantic Schemas ───────────────────

class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)


class TicketCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=4000)
    category: str = Field(default="General IT")
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")


# ───────────────────────────── Endpoints ─────────────────────────

@router.get("/health")
def health():
    """Public health check — no authentication required."""
    pg_active = is_postgres_available()
    redis_active = is_redis_available()
    return {
        "status": "ok",
        "service": settings.app_name,
        "database": "postgresql" if pg_active else "sqlite-fallback",
        "postgres_connected": pg_active,
        "cache": "redis" if redis_active else "in-memory-fallback",
        "redis_connected": redis_active,
        "observability": "langsmith" if is_langsmith_active() else "offline-tracing",
        "memory": "langmem" if settings.memory_enabled else "disabled",
        "llm_gateway": "litellm" if settings.litellm_enabled else "openai-direct",
        "model": settings.litellm_model if settings.litellm_enabled else settings.openai_model,
        "guardrails": settings.guardrails_enabled,
        "ragas": settings.ragas_enabled,
    }


@router.post("/chat")
def chat(
    payload: ChatRequest,
    current_user: TokenPayload = Depends(require_user),
):
    """
    Main chat endpoint — requires at least 'user' role.
    Checks Redis cache, runs Agentic RAG pipeline with LangMem memory,
    logs to PostgreSQL audit, and traces to LangSmith.
    """
    try:
        # 1. Check Redis cache first for instantaneous response
        cached = get_cached_response(payload.question)
        if cached:
            return {
                **cached,
                "user": current_user.sub,
                "role": current_user.role.value,
            }

        # 2. Execute RAG workflow with LangSmith tracing & LangMem user memory
        result = ask(payload.question, username=current_user.sub)

        # 3. Write to PostgreSQL audit log
        write_audit(
            question=payload.question,
            source_used=result["source_used"],
            trace=result.get("trace", []),
            username=current_user.sub,
            answer=result["answer"],
            guardrail_blocked=result.get("guardrail_blocked", False),
            citations=result.get("citations", []),
        )

        response_payload = {
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
            "guardrail_blocked": result.get("guardrail_blocked", False),
            "user": current_user.sub,
            "role": current_user.role.value,
            "cached": False,
        }

        # 4. Save to Redis cache if not blocked
        if not result.get("guardrail_blocked"):
            set_cached_response(payload.question, response_payload)

        return response_payload

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(require_admin),
):
    """
    Document ingestion — requires 'admin' role.
    Parses, chunks, embeds into Pinecone, and records catalog into PostgreSQL.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Supported: {', '.join(sorted(SUPPORTED))}")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    dest.write_bytes(await file.read())
    upload_to_s3_if_enabled(dest)
    docs = load_file(dest)
    chunks = chunk_documents(docs)
    ids = add_documents(chunks)

    # Persist document metadata in PostgreSQL / DB
    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            doc_record = KnowledgeDoc(
                filename=dest.name,
                file_type=suffix.lstrip("."),
                chunk_count=len(chunks),
                vector_ids_json=json.dumps(ids[:50]),
                ingested_by=current_user.sub,
            )
            session.add(doc_record)
            session.commit()
    except Exception:
        pass  # Non-blocking if DB table is unavailable

    return {
        "message": "Document indexed",
        "file": dest.name,
        "chunks": len(chunks),
        "ids_created": len(ids),
        "ingested_by": current_user.sub,
    }


@router.get("/audit")
def list_audit_logs(
    limit: int = 50,
    current_user: TokenPayload = Depends(require_analyst),
):
    """List recent query audit logs from PostgreSQL (requires analyst+ role)."""
    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            rows = (
                session.query(AuditLog)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "username": r.username,
                    "question": r.question,
                    "source_used": r.source_used,
                    "guardrail_blocked": r.guardrail_blocked,
                    "trace": json.loads(r.trace_json) if r.trace_json else [],
                }
                for r in rows
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch audit logs: {exc}")


# ───────────────────────────── IT Support Tickets ─────────────────

@router.post("/tickets")
def create_ticket(
    payload: TicketCreateRequest,
    current_user: TokenPayload = Depends(require_user),
):
    """Create an IT Support Ticket in PostgreSQL."""
    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            ticket = ITSupportTicket(
                username=current_user.sub,
                title=payload.title,
                description=payload.description,
                category=payload.category,
                priority=payload.priority,
                status="open",
            )
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return {
                "message": "Ticket created successfully",
                "ticket_number": ticket.ticket_number,
                "title": ticket.title,
                "status": ticket.status,
                "priority": ticket.priority,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create ticket: {exc}")


@router.get("/tickets")
def list_tickets(
    current_user: TokenPayload = Depends(require_user),
):
    """List IT Support tickets (users see their own; admin/analyst see all)."""
    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            query = session.query(ITSupportTicket).order_by(ITSupportTicket.created_at.desc())
            if current_user.role.value == "user":
                query = query.filter(ITSupportTicket.username == current_user.sub)
            tickets = query.limit(100).all()
            return [
                {
                    "id": t.id,
                    "ticket_number": t.ticket_number,
                    "username": t.username,
                    "title": t.title,
                    "category": t.category,
                    "priority": t.priority,
                    "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tickets
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tickets: {exc}")


# ───────────────────────────── LangMem User Memory ───────────────

@router.get("/memory")
def get_my_memory(
    current_user: TokenPayload = Depends(require_user),
):
    """View stored long-term context and preferences for the current user."""
    memories = get_user_memories(current_user.sub)
    return {
        "user": current_user.sub,
        "memory_count": len(memories),
        "memories": memories,
    }


@router.delete("/memory")
def clear_my_memory(
    current_user: TokenPayload = Depends(require_user),
):
    """Reset long-term context and preferences for the current user."""
    clear_user_memories(current_user.sub)
    return {
        "message": f"Memory profile for user '{current_user.sub}' cleared successfully.",
    }

