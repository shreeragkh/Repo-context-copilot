import logging
import re
from dataclasses import asdict
from typing import Any

from astrapy import DataAPIClient
from astrapy.constants import VectorMetric
from astrapy.info import CollectionDefinition
from sentence_transformers import SentenceTransformer

from config import settings
from db.chunking import Chunk

logger = logging.getLogger(__name__)


def sanitize_collection_name(repo_name: str) -> str:
    """AstraDB collection names must start with a letter and contain only
    letters/digits/underscores, <=48 chars. 'Hybrid-Search-RAG' -> 'hybrid_search_rag'."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", repo_name).lower()
    if not name or not name[0].isalpha():
        name = f"repo_{name}"
    return name[:48].rstrip("_") or "repo"


class VectorStore:
    """Wraps an AstraDB database. Each ingested repo gets its OWN collection
    (named after the repo) so repos are fully isolated from each other and can
    be dropped in a single call when their TTL expires."""

    def __init__(self):
        self.model = SentenceTransformer(settings.EMBEDDING_MODEL)
        self.client = DataAPIClient()
        self.db = self.client.get_database(
            api_endpoint=settings.API_ENDPOINT,
            token=settings.API_TOKEN,
        )

    def get_or_create_collection(self, collection_name: str):
        if collection_name not in self.db.list_collection_names():
            definition = (
                CollectionDefinition.builder()
                .with_vector_dimension(1024)
                .with_vector_metric(VectorMetric.COSINE)
                .build()
            )
            self.db.create_collection(collection_name, definition=definition)
            logger.info("Created AstraDB collection %s", collection_name, extra={"component": "vector_store"})
        return self.db.get_collection(collection_name)

    def insert_chunks(self, chunks: list[Chunk], collection_name: str, commit_sha: str,
                       batch_size: int = 50) -> int:
        collection = self.get_or_create_collection(collection_name)
        texts = [c.content for c in chunks]
        vectors = self.model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32,
        ).tolist()

        documents = [
            {"_id": c.chunk_id, "$vector": vec, "commit_sha": commit_sha, **asdict(c)}
            for vec, c in zip(vectors, chunks)
        ]

        inserted = 0
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            result = collection.insert_many(batch, request_timeout_ms=30000)
            inserted += len(result.inserted_ids)
        logger.info("Inserted %d chunks into collection %s", inserted, collection_name,
                    extra={"component": "vector_store"})
        return inserted

    def drop_collection(self, collection_name: str) -> None:
        try:
            self.db.drop_collection(collection_name)
            logger.info("Dropped AstraDB collection %s", collection_name, extra={"component": "vector_store"})
        except Exception:
            logger.exception("Failed to drop collection %s", collection_name, extra={"component": "vector_store"})

    def count(self, collection_name: str) -> int:
        try:
            collection = self.db.get_collection(collection_name)
            return collection.count_documents({}, upper_bound=100_000)
        except Exception:
            return -1

    def list_collections(self) -> list[str]:
        return self.db.list_collection_names()


class VectorRetriever:
    """Query-based semantic retrieval against ONE repo's collection; shaped to
    match BM25Retriever.query() output."""

    def __init__(self, vector_store: VectorStore, collection_name: str):
        self.collection = vector_store.db.get_collection(collection_name)
        self.model = vector_store.model
        self.collection_name = collection_name

    def query(self, query_text: str, k: int = 5) -> list[dict[str, Any]]:
        return self.retrieve(query_text, top_k=k)

    def retrieve(self, query: str, top_k: int = 5, score_threshold: float = 0.0) -> list[dict[str, Any]]:
        try:
            query_vector = self.model.encode([query], normalize_embeddings=True).tolist()[0]
            results = self.collection.find(
                sort={"$vector": query_vector}, limit=top_k, include_similarity=True,
            )
            retrieved = []
            for i, doc in enumerate(results):
                score = doc.get("$similarity", 0.0)
                if score < score_threshold:
                    continue
                content = doc.get("content", "")
                metadata = {k: v for k, v in doc.items() if k not in ("_id", "$vector", "$similarity", "content")}
                retrieved.append({
                    "id": doc.get("_id"),
                    "chunk_id": doc.get("chunk_id", doc.get("_id")),
                    "text": content,
                    "content": content,
                    "metadata": metadata,
                    "score": score,
                    "similarity_score": score,
                    "rank": i + 1,
                })
            return retrieved
        except Exception as e:
            logger.exception("Vector retrieval failed: %s", e, extra={"component": "vector_store"})
            return []
