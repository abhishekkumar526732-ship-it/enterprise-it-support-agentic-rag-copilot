from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────
    app_name: str = "Enterprise IT Support Agentic RAG Copilot"
    app_env: str = "development"

    # ── OpenAI (legacy direct calls / embeddings) ────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # ── Google Gemini via LiteLLM ────────────────────────────────
    gemini_api_key: str = ""
    litellm_model: str = "gemini/gemini-3.8-flash"
    litellm_fallback_model: str = "gpt-4o-mini"
    litellm_temperature: float = 0.0
    litellm_max_tokens: int = 4096
    litellm_timeout: int = 60
    litellm_enabled: bool = True

    # ── Tavily Web Search ────────────────────────────────────────
    tavily_api_key: str = ""

    # ── Pinecone Vector Store ────────────────────────────────────
    pinecone_api_key: str = ""
    pinecone_index_name: str = "fde-it-support-rag"
    pinecone_namespace: str = "company-it-kb"

    # ── RAG settings ─────────────────────────────────────────────
    top_k: int = 4
    max_retries: int = 1

    # ── Admin ingest key (legacy) ────────────────────────────────
    admin_api_key: str = "change-me"

    # ── Paths ────────────────────────────────────────────────────
    audit_db_path: str = str(BASE_DIR / "data" / "audit.db")
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")
    guardrails_config_dir: str = str(BASE_DIR / "config" / "guardrails")

    # ── JWT / OIDC / RBAC ────────────────────────────────────────
    jwt_secret_key: str = "super-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_minutes: int = 1440  # 24 hours

    # OIDC provider configuration (optional — set to enable SSO)
    oidc_enabled: bool = False
    oidc_discovery_url: str = ""          # e.g. https://accounts.google.com/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8080/api/auth/oidc/callback"

    # ── NeMo Guardrails ──────────────────────────────────────────
    guardrails_enabled: bool = True

    # ── Observability — LangSmith ────────────────────────────────
    observability_enabled: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "it-support-copilot"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_tracing: bool = True

    # ── In-Memory Cache & Session — Redis ────────────────────────
    redis_enabled: bool = True
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    redis_url: str = ""
    redis_cache_ttl: int = 3600  # default 1 hour TTL
    redis_connect_timeout: int = 2  # fast probe so app starts fast if Redis is offline

    @property
    def sync_redis_url(self) -> str:
        """Returns the Redis connection URL."""
        if self.redis_url:
            return self.redis_url
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Long-Term Agent Memory — LangMem ─────────────────────────
    memory_enabled: bool = True
    memory_max_items: int = 10

    # ── Ragas Evaluation ─────────────────────────────────────────
    ragas_enabled: bool = True

    # ── Relational Database — PostgreSQL ─────────────────────────
    postgres_enabled: bool = True
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "it_support_copilot"
    database_url: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_connect_timeout: int = 3
    db_echo: bool = False

    @property
    def sync_database_url(self) -> str:
        """Returns the PostgreSQL connection string or explicit database_url."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def fallback_sqlite_url(self) -> str:
        """SQLite fallback database URL when PostgreSQL is offline."""
        return f"sqlite:///{self.audit_db_path}"

    # ── AWS Cloud Storage (S3 for production uploads) ─────────────
    s3_enabled: bool = False
    s3_bucket_name: str = ""
    aws_region: str = "us-east-1"

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")





@lru_cache
def get_settings() -> Settings:
    return Settings()
