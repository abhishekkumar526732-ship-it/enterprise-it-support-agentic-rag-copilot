"""
SQLAlchemy ORM Models — PostgreSQL Storage
────────────────────────────────────────────
Models:
  1. User             — Persistent user credentials and RBAC roles
  2. AuditLog         — Complete query and trace audit logs
  3. ITSupportTicket  — Enterprise IT Helpdesk tickets
  4. EvaluationRun    — Ragas RAG pipeline evaluation metrics
  5. KnowledgeDoc     — Ingested document catalog and metadata
"""

from datetime import datetime, timezone
import uuid
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """
    Enterprise user account for JWT authentication and RBAC.
    Roles: admin, analyst, user
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="user")  # 'admin', 'analyst', 'user'
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    audit_logs = relationship("AuditLog", back_populates="user", cascade="all, delete-orphan")
    tickets = relationship("ITSupportTicket", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"


class AuditLog(Base):
    """
    Audit log storing every query, routing decision, guardrail check, and full trace.
    """
    __tablename__ = "query_audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100), nullable=True, index=True)
    question = Column(Text, nullable=False)
    source_used = Column(String(50), nullable=False)  # 'kb', 'web', 'direct', 'guardrail_blocked'
    answer = Column(Text, nullable=True)
    guardrail_blocked = Column(Boolean, default=False, nullable=False)
    citations_json = Column(Text, nullable=True)  # JSON-serialized list of citations
    trace_json = Column(Text, nullable=False)      # JSON-serialized list of pipeline trace steps
    execution_time_ms = Column(Integer, nullable=True)

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_user_created", "username", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, source='{self.source_used}', user='{self.username}')>"


class ITSupportTicket(Base):
    """
    IT Support Helpdesk Ticket created either directly by users or escalated
    automatically by the Copilot when an issue cannot be resolved.
    """
    __tablename__ = "it_support_tickets"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticket_number = Column(String(50), unique=True, nullable=False, index=True, default=lambda: f"INC-{uuid.uuid4().hex[:8].upper()}")
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String(100), nullable=False, default="General IT")  # 'Hardware', 'Software', 'Access/VPN', 'Network', etc.
    priority = Column(String(50), nullable=False, default="medium")       # 'low', 'medium', 'high', 'critical'
    status = Column(String(50), nullable=False, default="open")           # 'open', 'in_progress', 'resolved', 'closed'
    resolution = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="tickets")

    def __repr__(self) -> str:
        return f"<ITSupportTicket(number='{self.ticket_number}', status='{self.status}', priority='{self.priority}')>"


class EvaluationRun(Base):
    """
    Ragas RAG Evaluation result records for quality monitoring over time.
    """
    __tablename__ = "rag_evaluation_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    evaluated_by = Column(String(100), nullable=True, index=True)
    question = Column(Text, nullable=False)
    faithfulness = Column(Float, nullable=True)
    answer_relevancy = Column(Float, nullable=True)
    context_precision = Column(Float, nullable=True)
    context_recall = Column(Float, nullable=True)
    status = Column(String(50), default="completed", nullable=False)
    metrics_json = Column(Text, nullable=True)  # Full JSON metrics payload

    def __repr__(self) -> str:
        return f"<EvaluationRun(id={self.id}, faithfulness={self.faithfulness}, relevancy={self.answer_relevancy})>"


class KnowledgeDoc(Base):
    """
    Registry of documents uploaded and indexed into the RAG vector store.
    """
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    filename = Column(String(255), nullable=False, index=True)
    file_type = Column(String(50), nullable=False)  # 'pdf', 'docx', 'txt', 'md'
    chunk_count = Column(Integer, default=0, nullable=False)
    vector_ids_json = Column(Text, nullable=True)   # JSON list of vector ids in Pinecone
    ingested_by = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<KnowledgeDoc(id={self.id}, filename='{self.filename}', chunks={self.chunk_count})>"
