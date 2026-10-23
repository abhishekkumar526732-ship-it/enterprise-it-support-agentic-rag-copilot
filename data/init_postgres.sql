-- ═══════════════════════════════════════════════════════════════════
-- Enterprise IT Support Copilot — PostgreSQL Database Schema
-- ═══════════════════════════════════════════════════════════════════

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- Note: Vector storage is handled exclusively by Pinecone Serverless DB.
-- PostgreSQL is used strictly as the ACID relational store for users, tickets, and audits.

-- ── 1. Users Table (RBAC & Authentication) ─────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);
CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);

-- ── 2. Query Audit Logs Table ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_audit_logs (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username VARCHAR(100),
    question TEXT NOT NULL,
    source_used VARCHAR(50) NOT NULL,
    answer TEXT,
    guardrail_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    citations_json TEXT,
    trace_json TEXT NOT NULL,
    execution_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS ix_audit_username ON query_audit_logs(username);
CREATE INDEX IF NOT EXISTS ix_audit_created_at ON query_audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_user_created ON query_audit_logs(username, created_at);

-- ── 3. IT Support Tickets Table ────────────────────────────────────
CREATE TABLE IF NOT EXISTS it_support_tickets (
    id SERIAL PRIMARY KEY,
    ticket_number VARCHAR(50) UNIQUE NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username VARCHAR(100),
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(100) NOT NULL DEFAULT 'General IT',
    priority VARCHAR(50) NOT NULL DEFAULT 'medium',
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    resolution TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tickets_number ON it_support_tickets(ticket_number);
CREATE INDEX IF NOT EXISTS ix_tickets_username ON it_support_tickets(username);
CREATE INDEX IF NOT EXISTS ix_tickets_status ON it_support_tickets(status);

-- ── 4. Ragas Evaluation Runs Table ────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_evaluation_runs (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    evaluated_by VARCHAR(100),
    question TEXT NOT NULL,
    faithfulness DOUBLE PRECISION,
    answer_relevancy DOUBLE PRECISION,
    context_precision DOUBLE PRECISION,
    context_recall DOUBLE PRECISION,
    status VARCHAR(50) NOT NULL DEFAULT 'completed',
    metrics_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_eval_created_at ON rag_evaluation_runs(created_at);

-- ── 5. Knowledge Documents Catalog Table ───────────────────────────
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    vector_ids_json TEXT,
    ingested_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_docs_filename ON knowledge_documents(filename);

-- ── Seed Default Users (Passwords: admin123, analyst123, user123) ──
-- Bcrypt hashes generated for default passwords:
INSERT INTO users (username, email, hashed_password, role, is_active)
VALUES
    ('admin', 'admin@enterprise.local', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'admin', TRUE),
    ('analyst', 'analyst@enterprise.local', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'analyst', TRUE),
    ('user', 'user@enterprise.local', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'user', TRUE)
ON CONFLICT (username) DO NOTHING;
