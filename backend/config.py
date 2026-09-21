"""Centralized config. Import `settings` everywhere instead of calling os.getenv directly."""
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
load_dotenv()


class Settings:
    # Firebase
    FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY") or os.getenv("VITE_FIREBASE_API_KEY", "")
    FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN") or os.getenv("VITE_FIREBASE_AUTH_DOMAIN", "")
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID") or os.getenv("VITE_FIREBASE_PROJECT_ID", "")
    FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET") or os.getenv("VITE_FIREBASE_STORAGE_BUCKET", "")
    FIREBASE_MESSAGING_SENDER_ID = os.getenv("FIREBASE_MESSAGING_SENDER_ID") or os.getenv("VITE_FIREBASE_MESSAGING_SENDER_ID", "")
    FIREBASE_APP_ID = os.getenv("FIREBASE_APP_ID") or os.getenv("VITE_FIREBASE_APP_ID", "")
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
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


    # Local paths
    TEMP_REPO_ROOT = os.getenv("TEMP_REPO_ROOT", "./temp/repos")
    BM25_ROOT = os.getenv("BM25_ROOT", "./bm25_index")

    # Embeddings: bge-base @256 ingests ~3x faster than bge-large with equal post-rerank quality.
    EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
    EMBED_DIM = 768
    EMBED_MAX_SEQ = 256
    RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Chunking
    MAX_CHUNK_CHARS = 1500

    # Pipeline thresholds (tuned to MiniLM cross-encoder raw logits; recalibrate if reranker changes)
    RELEVANCE_THRESHOLD = -8.5


settings = Settings()
