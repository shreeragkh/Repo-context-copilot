import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from git import Repo as GitRepo

from config import settings
from db import BM25Retriever, HybridSearch, VectorRetriever, chunk_repo
from db.vector_store import sanitize_collection_name
from state import IngestedRepo, state

logger = logging.getLogger(__name__)


def repo_name_from_url(url: str) -> str:
    """Derive the repo name from a git URL without cloning it -
    used up-front to key the in-progress/registry dicts."""
    parsed = urlparse(url)
    return Path(parsed.path).stem


def clone_repo(url: str, dest_root: str = None) -> tuple[Path, str, str]:
    """Shallow-clone a github repo. Re-clones cleanly if the destination exists."""
    dest_root = dest_root or settings.TEMP_REPO_ROOT
    repo_name = repo_name_from_url(url)
    dest = Path(dest_root) / repo_name

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    repo = GitRepo.clone_from(url, str(dest), depth=1)
    commit_sha = repo.head.commit.hexsha[:12]
    return dest, repo_name, commit_sha


def _purge_repo(repo_name: str) -> None:
    """Tear down one ingested repo: vector collection, BM25 index, cloned
    files, and its cache entries. Safe to call even if partially ingested."""
    repo = state.repos.pop(repo_name, None)
    if repo is None:
        return
    logger.info("Purging repo %s@%s", repo.repo_name, repo.commit_sha, extra={"component": "ingestion"})

    if state.vector_store is not None:
        try:
            state.vector_store.drop_collection(repo.collection_name)
        except Exception:
            logger.exception("Failed to drop collection during purge", extra={"component": "ingestion"})

    if repo.bm25_retriever is not None:
        try:
            repo.bm25_retriever.delete()
        except Exception:
            logger.exception("Failed to delete BM25 index during purge", extra={"component": "ingestion"})

    repo_path = Path(repo.repo_path)
    if repo_path.exists():
        shutil.rmtree(repo_path, ignore_errors=True)

    if state.cache is not None:
        state.cache.purge_repo(repo.repo_name, repo.commit_sha)


def ingest_repo(repo_url: str) -> IngestedRepo:
    """Full ingestion pipeline for ONE repo: clone -> chunk -> embed+index ->
    bm25 -> register. Does NOT touch any other already-ingested repo. If this
    repo_name was already ingested, that prior version is purged first
    (re-ingest = replace)."""
    repo_name = repo_name_from_url(repo_url)

    if repo_name in state.ingest_in_progress:
        raise RuntimeError(f"Ingestion for '{repo_name}' is already in progress.")

    state.ingest_in_progress.add(repo_name)
    state.ingest_errors.pop(repo_name, None)
    try:
        if repo_name in state.repos:
            _purge_repo(repo_name)

        repo_path, repo_name, commit_sha = clone_repo(repo_url)
        logger.info("Cloned %s (%s) -> %s", repo_name, commit_sha, repo_path,
                    extra={"component": "ingestion"})

        chunks = chunk_repo(repo_path, repo_name)
        if not chunks:
            raise ValueError("No chunkable files found in this repository.")

        collection_name = sanitize_collection_name(repo_name)
        state.vector_store.insert_chunks(chunks, collection_name=collection_name, commit_sha=commit_sha)

        bm25_dir = f"{settings.BM25_ROOT}/{repo_name}"
        bm25 = BM25Retriever(index_dir=bm25_dir)
        bm25.build(chunks)
        bm25.save()

        vector_retriever = VectorRetriever(state.vector_store, collection_name)
        hybrid_search = HybridSearch(bm25, vector_retriever)

        now = datetime.now(timezone.utc)
        ingested = IngestedRepo(
            repo_name=repo_name, collection_name=collection_name, commit_sha=commit_sha,
            repo_path=str(repo_path), bm25_dir=bm25_dir, chunk_count=len(chunks),
            ingested_at=now, expires_at=now + timedelta(minutes=settings.REPO_TTL_MINUTES),
            bm25_retriever=bm25, vector_retriever=vector_retriever, hybrid_search=hybrid_search,
        )

        state.repos[repo_name] = ingested
        logger.info("Ingestion complete for %s: %d chunks, expires at %s",
                    repo_name, len(chunks), ingested.expires_at, extra={"component": "ingestion"})
        return ingested
    except Exception as e:
        state.ingest_errors[repo_name] = str(e)
        logger.exception("Ingestion failed for %s", repo_url, extra={"component": "ingestion"})
        raise
    finally:
        state.ingest_in_progress.discard(repo_name)
