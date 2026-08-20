"""Centralized config. Import `settings` everywhere instead of calling os.getenv directly."""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Firebase
    FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "")
    FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "")
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

    # AstraDB
    API_ENDPOINT = os.getenv("API_ENDPOINT", "")
    API_TOKEN = os.getenv("API_TOKEN", "")
    # NOTE: no single COLLECTION_NAME anymore - each ingested repo gets its
    # own AstraDB collection, named after the repo (see db/vector_store.py).

    # Redis
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

    # LLM
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    GROQ_FREE_MODELS = [
        m.strip() for m in os.getenv(
            "GROQ_FREE_MODELS", "llama-3.3-70b-versatile,qwen/qwen3-32b"
        ).split(",") if m.strip()
    ]
    CLASSIFIER_PAID_MODEL = os.getenv("CLASSIFIER_PAID_MODEL", "gpt-5-nano")
    GENERATION_PAID_MODEL = os.getenv("GENERATION_PAID_MODEL", "gpt-5-mini")

    # Behaviour
    LOG_COMPARISON_MODE = os.getenv("LOG_COMPARISON_MODE", "true").lower() == "true"
    REPO_TTL_MINUTES = int(os.getenv("REPO_TTL_MINUTES", "60"))
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

    # Local paths
    TEMP_REPO_ROOT = os.getenv("TEMP_REPO_ROOT", "./temp/repos")
    BM25_ROOT = os.getenv("BM25_ROOT", "./bm25_index")

    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")


settings = Settings()
