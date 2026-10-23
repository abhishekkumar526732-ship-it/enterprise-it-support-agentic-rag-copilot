"""
Diagnostic Utility — Verify Application Setup & Integrations
─────────────────────────────────────────────────────────────
Runs quick operational checks on:
  1. Environment configuration (.env)
  2. Database connectivity (PostgreSQL / SQLite fallback)
  3. Redis Cache connectivity (with fallback detection)
  4. Vector Store configuration (Pinecone index & dimensions)
  5. LLM Gateway & Guardrails status

Usage:
  python scripts/verify_setup.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    try:
        from app.core.config import get_settings
    except ImportError as e:
        print(f"[!] Core dependencies missing ({e}). Run: pip install -r requirements.txt")
        return

    settings = get_settings()
    print("=" * 60)
    print(f" {settings.app_name}")
    print(" Environment & Service Verification")
    print("=" * 60)

    # 1. Database
    try:
        from app.db.session import is_postgres_available
        pg_status = is_postgres_available()
        if pg_status:
            print("[✓] Database: PostgreSQL connected successfully.")
        else:
            print(f"[!] Database: PostgreSQL not reachable -> using SQLite fallback ({settings.fallback_sqlite_url}).")
    except Exception as e:
        print(f"[!] Database check: {e}")

    # 2. Redis
    try:
        from app.core.redis_client import is_redis_available
        redis_status = is_redis_available()
        if redis_status:
            print("[✓] Cache: Redis connected successfully.")
        else:
            print("[!] Cache: Redis not reachable -> using Thread-Safe In-Memory Cache.")
    except Exception as e:
        print(f"[!] Redis check: {e}")

    # 3. Observability
    try:
        from app.core.observability import is_langsmith_active
        if is_langsmith_active():
            print(f"[✓] Observability: LangSmith active (Project: {settings.langsmith_project}).")
        else:
            print("[!] Observability: LangSmith offline / not configured.")
    except Exception as e:
        print(f"[!] Observability check: {e}")

    # 4. LLM & Embeddings
    print(f"[✓] LLM Gateway: LiteLLM enabled={settings.litellm_enabled} (Model: {settings.litellm_model})")
    try:
        from app.rag.vectorstore import get_embedding_dimension
        dim = get_embedding_dimension()
        print(f"[✓] Embeddings: {settings.embedding_model} (Dimensions: {dim})")
    except Exception as e:
        print(f"[!] Embeddings: {e}")

    # 5. Pinecone
    if settings.pinecone_api_key:
        print(f"[✓] Pinecone: Configured (Index: {settings.pinecone_index_name}, Namespace: {settings.pinecone_namespace})")
    else:
        print("[!] Pinecone: PINECONE_API_KEY is not set.")

    # 6. NeMo Guardrails
    print(f"[✓] Safety Rails: NeMo Guardrails enabled={settings.guardrails_enabled}")

    print("=" * 60)
    print("Verification complete.\n")


if __name__ == "__main__":
    main()
