"""
Global application state.

Multiple repos can be ingested and live at once, each fully isolated:
own AstraDB collection, own BM25 index directory, own TTL. `state.repos` is
keyed by repo_name -> IngestedRepo, which carries that repo's own
hybrid_search pipeline so queries can pick which repo to search.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IngestedRepo:
    repo_name: str
    collection_name: str
    commit_sha: str
    repo_path: str
    bm25_dir: str
    chunk_count: int
    ingested_at: datetime
    expires_at: datetime

    bm25_retriever: Any = None
    vector_retriever: Any = None
    hybrid_search: Any = None


@dataclass
class AppState:
    vector_store: Any = None
    reranker: Any = None
    cache: Any = None

    # repo_name -> IngestedRepo
    repos: dict[str, IngestedRepo] = field(default_factory=dict)
    # repo_names currently being ingested (dedupe/guard against double-ingest)
    ingest_in_progress: set[str] = field(default_factory=set)
    # repo_name -> last ingestion error message
    ingest_errors: dict[str, str] = field(default_factory=dict)


state = AppState()
