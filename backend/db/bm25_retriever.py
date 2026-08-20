import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import bm25s

logger = logging.getLogger(__name__)


class BM25Retriever:
    def __init__(self, index_dir: str | Path = "bm25_index"):
        self.index_dir = Path(index_dir)
        self.retriever: bm25s.BM25 | None = None
        self.corpus: list[str] = []
        self.metadata: list[dict[str, Any]] = []

    @staticmethod
    def _to_dicts(chunks: list[Any]) -> list[dict[str, Any]]:
        return [asdict(c) if is_dataclass(c) else c for c in chunks]

    def build(self, chunks: list[Any], text_key: str = "content") -> None:
        chunk_dicts = self._to_dicts(chunks)
        if not chunk_dicts:
            raise ValueError("Cannot build BM25 index from an empty chunk list.")

        self.corpus = [c[text_key] for c in chunk_dicts]
        self.metadata = [{k: v for k, v in c.items() if k != text_key} for c in chunk_dicts]

        tokens = bm25s.tokenize(self.corpus, show_progress=False)
        self.retriever = bm25s.BM25()
        self.retriever.index(tokens, show_progress=False)
        logger.info("BM25 index built with %d documents", len(self.corpus), extra={"component": "bm25"})

    def save(self) -> None:
        if self.retriever is None:
            raise RuntimeError("No index to save. Call build() first.")
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.retriever.save(str(self.index_dir), corpus=self.corpus)
        with open(self.index_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(self.metadata, f)

    def load(self) -> None:
        if not self.index_dir.exists():
            raise FileNotFoundError(f"No index found at {self.index_dir}")
        self.retriever = bm25s.BM25.load(str(self.index_dir), load_corpus=True)
        meta_path = self.index_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)
        else:
            self.metadata = [{} for _ in range(len(self.retriever.corpus))]
        self.corpus = [doc["text"] if isinstance(doc, dict) else doc for doc in self.retriever.corpus]
        self.retriever.corpus = None

    def query(self, query_text: str, k: int = 10) -> list[dict[str, Any]]:
        if self.retriever is None:
            raise RuntimeError("Index not built or loaded. Call build() or load() first.")
        k = min(k, len(self.corpus))
        if k == 0:
            return []
        query_tokens = bm25s.tokenize(query_text, show_progress=False)
        doc_indices, scores = self.retriever.retrieve(query_tokens, k=k, show_progress=False)
        results = []
        for idx, score in zip(doc_indices[0], scores[0]):
            idx = int(idx)
            meta = self.metadata[idx] if idx < len(self.metadata) else {}
            results.append({
                "text": self.corpus[idx],
                "score": float(score),
                "chunk_id": meta.get("chunk_id"),
                "metadata": meta,
            })
        return results

    def __len__(self) -> int:
        return len(self.corpus)

    def delete(self) -> None:
        """Remove the on-disk index directory entirely."""
        import shutil
        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)
