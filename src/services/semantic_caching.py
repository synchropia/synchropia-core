"""
services/semantic_caching.py
-----------------------------
Semantic caching layer backed by Redis native Vector Similarity Search (VSS).

Architecture:
  - Storage:    Redis Hashes under prefix `semantic_cache:entry:`
  - Index:      HNSW vector index named `semantic_cache_idx` managed by redisvl
  - Embedding:  Cohere embed-english-v3.0 (1024 dimensions, float32)
  - Similarity: COSINE (Redis VSS returns distance = 1 - similarity)
  - Lookup:     Single native KNN query — O(log N) vs previous O(N) client-side scan

Migration from previous implementation:
  OLD: Keys(prefix*) → pipeline.get(all) → Python cosine loop — O(N), scales badly
  NEW: VectorQuery KNN 1 inside Redis HNSW engine — O(log N), sub-10ms at 5k+ entries
"""

import struct
import hashlib
import asyncio
from typing import Optional

from langchain_cohere import CohereEmbeddings

from src.config.settings import settings
from src.services.redis_client import redis_client
from src.utils.logger import logger

# ---------------------------------------------------------------------------
# Lazy imports for redisvl — only SearchIndex + IndexSchema are needed now.
# lookup() uses raw execute_command("FT.SEARCH", ...) which avoids the
# VectorQuery parameter-encoding incompatibility with binary_client.
# ---------------------------------------------------------------------------
try:
    from redisvl.index import SearchIndex
    from redisvl.schema import IndexSchema
    _REDISVL_AVAILABLE = True
except ImportError:
    _REDISVL_AVAILABLE = False
    SearchIndex = None
    IndexSchema = None
    logger.warning(
        "redisvl package not found. Semantic cache VSS is disabled. "
        "Install with: uv add redisvl"
    )

# ---------------------------------------------------------------------------
# HNSW Index Schema
# ---------------------------------------------------------------------------
# This dict is parsed by redisvl's IndexSchema to create the Redis FT index.
#
# Key design decisions:
#   storage_type = "hash"  → entries are Redis Hashes (not JSON docs)
#   algorithm    = "hnsw"  → Hierarchical Navigable Small World graph, O(log N) KNN
#   datatype     = "float32" → must match struct.pack("<...f") format in _floats_to_bytes()
#   distance_metric = "cosine" → Redis returns distance (0=identical), NOT similarity
#   m = 16       → bi-directional links per HNSW node (accuracy vs memory tradeoff)
#   ef_construction = 200 → candidate list size during index build (higher=more accurate)
_CACHE_INDEX_SCHEMA = {
    "index": {
        "name": "semantic_cache_idx",
        "prefix": "semantic_cache:entry:",
        "storage_type": "hash",
    },
    "fields": [
        {"name": "prompt", "type": "text"},
        {"name": "response", "type": "text"},
        {
            "name": "embedding",
            "type": "vector",
            "attrs": {
                "algorithm": "hnsw",
                "datatype": "float32",
                "dims": settings.COHERE_EMBED_DIMENSION,
                "distance_metric": "cosine",
                "m": 16,
                "ef_construction": 200,
            },
        },
    ],
}


def _floats_to_bytes(vector: list[float]) -> bytes:
    """
    Convert a list of Python floats to a little-endian IEEE 754 float32 binary blob.

    Redis VSS HNSW engine reads the embedding field as raw bytes in float32 format.
    struct.pack("<{n}f", *vector) packs n floats as little-endian 32-bit IEEE 754
    values — exactly what the HNSW engine expects.

    Note: This is why binary_client (decode_responses=False) must be used when
    writing the Hash — the standard sync_client (decode_responses=True) would
    attempt UTF-8 decoding on the binary blob and corrupt or error on it.

    Args:
        vector: Float list from Cohere embeddings API (e.g., 1024 values)

    Returns:
        Raw bytes of length len(vector) * 4
    """
    return struct.pack(f"<{len(vector)}f", *vector)


class SemanticCache:
    """
    High-performance semantic caching layer using Redis native HNSW Vector Search.

    Replaces the previous O(N) client-side cosine similarity implementation with
    a single O(log N) KNN query executed entirely inside the Redis HNSW engine.

    Storage layout (Redis Hash per entry):
        Key:     semantic_cache:entry:<sha256_of_prompt>
        Fields:
          prompt    → UTF-8 string (original user prompt)
          response  → UTF-8 string (agent response to return on cache hit)
          embedding → float32 binary blob (indexed by HNSW, not returned to client)

    The HNSW index `semantic_cache_idx` is managed by redisvl and automatically
    maintained as Hashes are created/deleted under the key prefix.

    Fallback behaviour:
        If redisvl is not installed OR if Redis Stack (Search module) is unavailable,
        all lookups return None (cache miss) and all sets return False. The agents
        continue running normally — no crash, just no caching benefit.
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        ttl: Optional[int] = None,
    ):
        self.threshold = threshold or settings.SEMANTIC_CACHE_THRESHOLD  # default 0.95
        self.ttl = ttl or settings.SEMANTIC_CACHE_TTL                    # default 86400s
        self.prefix = "semantic_cache:entry:"
        self._index = None       # redisvl SearchIndex instance (created lazily)
        self._index_ready = False

        logger.info(
            f"Initializing VSS Semantic Cache ("
            f"threshold={self.threshold}, ttl={self.ttl}s, "
            f"dims={settings.COHERE_EMBED_DIMENSION}, algorithm=HNSW)"
        )

        # Initialize Cohere embeddings client
        try:
            self.embeddings = CohereEmbeddings(
                model=settings.COHERE_EMBED_MODEL,
                cohere_api_key=settings.COHERE_API_KEY,
            )
            logger.success(f"Embedding model ready: {settings.COHERE_EMBED_MODEL} ({settings.COHERE_EMBED_DIMENSION} dims)")
        except Exception as e:
            logger.error(f"Failed to initialize CohereEmbeddings: {e}. Semantic cache disabled.")
            self.embeddings = None

    # -------------------------------------------------------------------------
    # Internal: index lifecycle
    # -------------------------------------------------------------------------

    def _flush_old_string_entries(self) -> None:
        """
        Flush legacy STRING-type cache entries from previous implementation.

        The old SemanticCache stored entries as Redis STRINGs (JSON blobs).
        Redis cannot store both STRING and HASH under the same key, so if we
        try to hset a key that was previously a STRING it raises WRONGTYPE error.
        This one-time migration step detects and clears old-format keys.
        """
        try:
            old_keys = redis_client.sync_client.keys(f"{self.prefix}*")
            if not old_keys:
                return
            # Check type of first key — if STRING, it's old format
            key_type = redis_client.sync_client.type(old_keys[0])
            if key_type == "string":
                logger.info(
                    f"Detected {len(old_keys)} legacy STRING-format cache entries. "
                    f"Flushing to migrate to HASH format for HNSW index..."
                )
                redis_client.sync_client.delete(*old_keys)
                logger.success("Legacy STRING entries flushed. HNSW migration clean.")
        except Exception as e:
            logger.warning(f"Could not flush legacy cache entries: {e}")

    def _ensure_index(self) -> bool:
        """
        Idempotently create or verify the Redis HNSW vector index.

        Uses redisvl SearchIndex.create(overwrite=False) which is a no-op if the
        index already exists with a compatible schema. This is safe to call on
        every request — it short-circuits immediately after the first success.

        Returns:
            True if the index is ready for queries.
            False if redisvl is missing or Redis lacks the Search module.
        """
        if self._index_ready:
            return True

        if not _REDISVL_AVAILABLE:
            logger.warning("redisvl not available — VSS cache disabled.")
            return False

        try:
            # Flush any old STRING-format entries to avoid WRONGTYPE conflicts
            self._flush_old_string_entries()

            schema = IndexSchema.from_dict(_CACHE_INDEX_SCHEMA)
            # Connect the SearchIndex to the binary_client pool (decode_responses=False)
            # so redisvl can write binary embedding blobs without UTF-8 corruption.
            self._index = SearchIndex(schema, redis_client=redis_client.binary_client)
            self._index.create(overwrite=False)
            self._index_ready = True
            logger.success("Redis HNSW index 'semantic_cache_idx' is ready.")
            return True

        except Exception as e:
            logger.warning(
                f"Failed to initialize Redis VSS index: {e}. "
                f"Verify Redis Stack is running: docker exec synchropia-redis "
                f"redis-cli MODULE LIST | grep search"
            )
            self._index_ready = False
            return False

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def lookup(self, prompt: str) -> Optional[str]:
        """
        Execute a native Redis KNN vector search for a semantically similar cached prompt.

        Replaces the previous O(N) implementation which:
          1. Fetched all keys via SCAN/KEYS
          2. Pulled all embeddings over the network via pipeline
          3. Computed cosine similarity in Python for every entry

        New implementation:
          1. Embeds the prompt via Cohere API (one async HTTP call)
          2. Executes one Redis KNN query: "*=>[KNN 1 @embedding $vec AS score]"
          3. Converts returned COSINE DISTANCE → similarity (similarity = 1 - distance)
          4. Returns cached response if similarity >= threshold

        Args:
            prompt: The raw user prompt string to look up in the cache.

        Returns:
            Cached response string if similarity >= threshold, else None.
        """
        logger.info(f"VSS cache lookup: '{prompt[:60]}...'")

        if not self.embeddings:
            return None

        if not self._ensure_index():
            logger.warning("VSS index not ready — skipping cache lookup.")
            return None

        try:
            # 1. Generate query embedding vector (async Cohere API call)
            query_vector: list[float] = await self.embeddings.aembed_query(prompt)
            query_bytes: bytes = _floats_to_bytes(query_vector)

            # 2. Execute native KNN query via raw FT.SEARCH command.
            #    Using execute_command directly (not redisvl VectorQuery) because
            #    redisvl's query builder has a parameter-passing incompatibility
            #    with binary_client (decode_responses=False). The raw command
            #    inlines the bytes directly — proven working by the benchmark.
            #
            #    FT.SEARCH syntax:
            #      "*=>[KNN 1 @embedding $vec AS score]"
            #      PARAMS 2 vec <bytes>   → supplies the query vector
            #      RETURN 2 response score → only fetch needed fields
            #      SORTBY score            → nearest first
            #      LIMIT 0 1              → top-1 only
            #      DIALECT 2              → required for vector queries
            raw = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: redis_client.binary_client.execute_command(
                    "FT.SEARCH", "semantic_cache_idx",
                    "*=>[KNN 1 @embedding $vec AS score]",
                    "PARAMS", "2", "vec", query_bytes,
                    "RETURN", "2", "response", "score",
                    "SORTBY", "score",
                    "LIMIT", "0", "1",
                    "DIALECT", "2",
                )
            )

            # raw format: [total_count, key, [field, val, field, val...], ...]
            if not raw or raw[0] == 0:
                logger.info("VSS cache miss — index is empty.")
                return None

            fields = raw[2]
            fd: dict = {}
            for i in range(0, len(fields) - 1, 2):
                fd[fields[i]] = fields[i + 1]

            # 3. Convert COSINE DISTANCE → COSINE SIMILARITY
            #    Redis returns distance: 0=identical, 1=orthogonal, 2=opposite
            raw_distance = float(fd.get(b"score", 1.0))
            similarity = 1.0 - raw_distance

            response_bytes = fd.get(b"response", b"")
            response_str = response_bytes.decode("utf-8") if isinstance(response_bytes, bytes) else str(response_bytes)

            logger.debug(
                f"VSS result: distance={raw_distance:.4f} -> similarity={similarity:.4f} "
                f"| threshold={self.threshold}"
            )

            if similarity >= self.threshold:
                logger.success(
                    f"VSS cache HIT! similarity={similarity:.4f} >= threshold={self.threshold}"
                )
                return response_str

            logger.info(
                f"VSS cache miss. Best similarity={similarity:.4f} < threshold={self.threshold}"
            )
            return None

        except Exception as e:
            logger.error(f"VSS lookup error: {e}")
            return None

    async def set(self, prompt: str, response: str) -> bool:
        """
        Store a prompt-response pair as a Redis Hash, automatically indexed by HNSW.

        Redis automatically picks up new Hashes matching the index prefix
        (`semantic_cache:entry:`) and adds them to the HNSW graph. No manual
        index-add step is required.

        Key is sha256(prompt) to ensure deterministic deduplication — setting
        the same prompt twice overwrites the previous entry rather than duplicating.

        IMPORTANT: Uses binary_client (decode_responses=False) for the hset call
        so the float32 embedding bytes are stored verbatim without UTF-8 decoding.

        Args:
            prompt:   User's original prompt string.
            response: Agent's generated response to cache.

        Returns:
            True on success, False on failure.
        """
        logger.info(f"VSS cache set: '{prompt[:60]}...'")

        if not self.embeddings:
            return False

        if not self._ensure_index():
            return False

        try:
            # 1. Generate embedding for the prompt (async Cohere API call)
            vector: list[float] = await self.embeddings.aembed_query(prompt)
            embedding_bytes: bytes = _floats_to_bytes(vector)

            # 2. Deterministic key from sha256 of prompt text
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            key = f"{self.prefix}{prompt_hash}"

            # 3. Store as Redis Hash via binary_client
            #    binary_client has decode_responses=False so embedding bytes
            #    are stored as-is — the HNSW engine reads them directly.
            pipe = redis_client.binary_client.pipeline()
            pipe.hset(key, mapping={
                "prompt": prompt.encode("utf-8"),
                "response": response.encode("utf-8"),
                "embedding": embedding_bytes,
            })
            # Apply TTL only if positive (SEMANTIC_CACHE_TTL=-1 means infinite)
            if self.ttl and self.ttl > 0:
                pipe.expire(key, self.ttl)
            pipe.execute()

            logger.success(
                f"VSS cache entry stored: key='{key[-12:]}...' | "
                f"dims={len(vector)} | TTL={self.ttl}s"
            )
            return True

        except Exception as e:
            logger.error(f"VSS cache set error: {e}")
            return False

    def clear(self) -> int:
        """
        Delete all semantic cache Hash entries and drop the HNSW index.

        Steps:
          1. Delete all Hash keys under `semantic_cache:entry:*`
          2. Drop the HNSW index `semantic_cache_idx` (will be recreated on next use)

        Returns:
            Number of Hash keys deleted.
        """
        logger.info("Clearing VSS semantic cache...")
        deleted = 0

        try:
            # 1. Delete all entry keys
            keys = redis_client.sync_client.keys(f"{self.prefix}*")
            if keys:
                deleted = redis_client.sync_client.delete(*keys)
                logger.success(f"Deleted {deleted} semantic cache keys.")
            else:
                logger.info("No cache keys found to delete.")

            # 2. Drop the index so it's recreated fresh on next _ensure_index()
            if self._index and self._index_ready:
                try:
                    # drop=False preserves the underlying Hash data (already deleted above)
                    # drop=True would also delete the keys — either is fine since we cleared them
                    self._index.delete(drop=False)
                    self._index_ready = False
                    self._index = None
                    logger.info("HNSW index dropped — will be recreated on next use.")
                except Exception as ie:
                    logger.warning(f"Could not drop index (may not exist yet): {ie}")

        except Exception as e:
            logger.error(f"VSS cache clear error: {e}")

        return deleted


# Module-level singleton — same interface as before, drop-in replacement
semantic_cache = SemanticCache()

__all__ = ["SemanticCache", "semantic_cache"]
