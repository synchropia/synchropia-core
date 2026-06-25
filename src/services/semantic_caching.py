import json
import hashlib
import math
import asyncio
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Import langchain, settings and logger
from src.core.get_embedding import OpenRouterEmbeddings
from src.config.settings import settings
from src.services.redis_client import RedisAPIClient
from src.utils.logger import logger  

load_dotenv()

class SemanticCache:
    """A semantic caching layer for AI Agents backed by Redis and OpenRouter Embeddings.
    
    Performs cosine similarity checks on vector representations of prompts.
    """
    
    def __init__(
        self,
        redis_client: Optional[RedisAPIClient] = None,
        threshold: Optional[float] = None,
        ttl: Optional[int] = None,
        embedding_model: str = "models/text-embedding-004"
    ):
        self.redis_client = redis_client or RedisAPIClient()
        self.threshold = threshold or settings.SEMANTIC_CACHE_THRESHOLD
        self.ttl = ttl or settings.SEMANTIC_CACHE_TTL
        self.prefix = "semantic_cache:entry:"
        
        logger.info(
            f"Initializing Semantic Cache (threshold={self.threshold}, "
            f"ttl={self.ttl}s, prefix='{self.prefix}')"
        )
        
        # Initialize OpenRouter Embeddings client
        try:
            self.embeddings = OpenRouterEmbeddings()
            logger.success(f"Semantic Cache initialized with embedding model: {embedding_model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenRouterEmbeddings: {e}. Semantic cache will fallback to cache misses.")
            self.embeddings = None

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """Calculate the cosine similarity between two vectors."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot_product = sum(x * y for x, y in zip(v1, v2))
        magnitude1 = math.sqrt(sum(x * x for x in v1))
        magnitude2 = math.sqrt(sum(x * x for x in v2))
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        return dot_product / (magnitude1 * magnitude2)

    async def lookup(self, prompt: str) -> Optional[str]:
        """Perform a semantic lookup in the cache for the given prompt.
        
        Returns the cached response string if a match above the threshold is found, otherwise None.
        """
        logger.info(f"Initiating semantic cache lookup for prompt: '{prompt[:60]}...'")
        
        if not self.embeddings:
            logger.warning("Embeddings model not initialized. Skipping cache lookup.")
            return None
            
        try:
            # 1. Generate embedding for the incoming query
            logger.debug("Generating query embedding via OpenRouter...")
            query_vector = await self.embeddings.aembed_query(prompt)
            logger.debug(f"Query embedding generated successfully. Dimension: {len(query_vector)}")
            
            # 2. Retrieve all keys from Redis matching the prefix
            sync_redis = self.redis_client.sync_client
            keys = sync_redis.keys(f"{self.prefix}*")
            if not keys:
                logger.info("Semantic cache has no entries. Cache miss.")
                return None
                
            logger.info(f"Found {len(keys)} entries in semantic cache. Fetching and comparing embeddings...")
            
            # 3. Retrieve all cache entries using a Redis pipeline for efficiency
            pipe = sync_redis.pipeline()
            for key in keys:
                pipe.get(key)
            raw_entries = pipe.execute()
            
            # 4. Search for the best match using cosine similarity
            best_match: Optional[Dict[str, Any]] = None
            highest_score = -1.0
            
            for key, raw_entry in zip(keys, raw_entries):
                if not raw_entry:
                    continue
                try:
                    entry = json.loads(raw_entry)
                    cached_vector = entry.get("embedding")
                    score = self._cosine_similarity(query_vector, cached_vector)
                    
                    logger.debug(f"Similarity with '{entry.get('prompt')[:30]}...': {score:.4f}")
                    
                    if score > highest_score:
                        highest_score = score
                        best_match = entry
                except Exception as entry_err:
                    logger.warning(f"Error parsing semantic cache entry '{key}': {entry_err}")
                    continue
            
            # 5. Check if the similarity exceeds our threshold
            if best_match and highest_score >= self.threshold:
                logger.success(
                    f"Semantic cache hit! Similarity score: {highest_score:.4f} "
                    f"(threshold: {self.threshold:.2f}). Matched query: '{best_match.get('prompt')}'"
                )
                return best_match.get("response")
                
            logger.info(
                f"Semantic cache miss. Best match score: {highest_score:.4f} "
                f"(threshold: {self.threshold:.2f})."
            )
            return None
            
        except Exception as e:
            logger.error(f"Error during semantic cache lookup: {e}")
            return None

    async def set(self, prompt: str, response: str) -> bool:
        """Store a prompt-response pair and its vector embedding in the Redis cache."""
        logger.info(f"Preparing to cache prompt: '{prompt[:60]}...'")
        
        if not self.embeddings:
            logger.warning("Embeddings model not initialized. Skipping cache write.")
            return False
            
        try:
            # 1. Generate embedding for the prompt
            logger.debug("Generating prompt embedding via OpenRouter...")
            vector = await self.embeddings.aembed_query(prompt)
            logger.debug(f"Prompt embedding generated successfully. Dimension: {len(vector)}")
            
            # 2. Prepare payload
            payload = {
                "prompt": prompt,
                "response": response,
                "embedding": vector
            }
            
            # 3. Generate key based on prompt hash
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            key = f"{self.prefix}{prompt_hash}"
            
            # 4. Store in Redis with TTL
            sync_redis = self.redis_client.sync_client
            sync_redis.setex(key, self.ttl, json.dumps(payload))
            logger.success(f"Successfully cached response semantically under key: '{key}' with TTL: {self.ttl}s.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write to semantic cache: {e}")
            return False

    def clear(self) -> int:
        """Clear all entries in the semantic cache. Returns the number of keys deleted."""
        logger.info("Clearing semantic cache entries...")
        try:
            sync_redis = self.redis_client.sync_client
            keys = sync_redis.keys(f"{self.prefix}*")
            if keys:
                deleted = sync_redis.delete(*keys)
                logger.success(f"Cleared {deleted} keys from the semantic cache.")
                return deleted
            logger.info("No keys found to clear in semantic cache.")
            return 0
        except Exception as e:
            logger.error(f"Failed to clear semantic cache: {e}")
            return 0

semantic_cache = SemanticCache()

__all__ = ["SemanticCache", "semantic_cache"]