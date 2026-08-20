import hashlib
import json
import logging

import redis

from config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h; also hard-purged when a repo expires


class RetrievalCache:
    def __init__(self):
        self.client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )

    def _key(self, repo_name: str, commit_sha: str, query: str) -> str:
        raw = f"{repo_name}:{commit_sha}:{query.strip().lower()}"
        return "ragcache:" + hashlib.sha256(raw.encode()).hexdigest()

    def get(self, repo_name: str, commit_sha: str, query: str):
        try:
            cached = self.client.get(self._key(repo_name, commit_sha, query))
            return json.loads(cached) if cached else None
        except redis.RedisError as e:
            logger.warning("Redis unavailable on read: %s", e, extra={"component": "cache"})
            return None

    def set(self, repo_name: str, commit_sha: str, query: str, value: dict):
        try:
            key = self._key(repo_name, commit_sha, query)
            self.client.setex(key, CACHE_TTL_SECONDS, json.dumps(value))
            # track keys per repo so we can purge them on ingestion cleanup
            self.client.sadd(f"ragcache:keys:{repo_name}:{commit_sha}", key)
        except redis.RedisError as e:
            logger.warning("Redis unavailable on write: %s", e, extra={"component": "cache"})

    def purge_repo(self, repo_name: str, commit_sha: str) -> int:
        """Delete all cached entries belonging to a specific ingested repo."""
        try:
            index_key = f"ragcache:keys:{repo_name}:{commit_sha}"
            keys = self.client.smembers(index_key)
            if keys:
                self.client.delete(*keys)
            self.client.delete(index_key)
            return len(keys)
        except redis.RedisError as e:
            logger.warning("Redis unavailable on purge: %s", e, extra={"component": "cache"})
            return 0

    def clear_all(self):
        try:
            for key in self.client.scan_iter("ragcache:*"):
                self.client.delete(key)
        except redis.RedisError as e:
            logger.warning("Redis unavailable on clear: %s", e, extra={"component": "cache"})

    def health(self) -> bool:
        try:
            return self.client.ping()
        except redis.RedisError:
            return False


retrieval_cache = RetrievalCache()
