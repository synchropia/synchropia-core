import time
import sqlite3
from typing import Dict, List, Optional
from loguru import logger
from src.services.redis_client import redis_client

class RedisRateLimiter:
    """Sliding window rate limiter backed by Redis with local SQLite database fallback."""

    def __init__(self,db_path: str = "synchropia_rate_limit.db"):
        self.redis_client = redis_client
        self.db_path = db_path
        
        # Initialize SQLite database schema
        self._init_sqlite_db()
        
        if self.redis_client is None:
            logger.warning(f"[RateLimiter] Redis client not provided. Falling back to local SQLite rate limiting (db: {self.db_path}).")

    def _init_sqlite_db(self):
        """Initialize the SQLite schema and indexes for query optimization."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS request_logs ("
                    "client_id TEXT, "
                    "timestamp REAL"
                    ")"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_client_timestamp "
                    "ON request_logs(client_id, timestamp)"
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[RateLimiter] Failed to initialize SQLite database: {e}")

    def is_allowed(self, client_id: str, limit: int, window_seconds: int) -> bool:
        """Determine if a client request is allowed under the sliding window limit.
        
        Args:
            client_id: Unique identifier for the client (e.g. user ID or agent name).
            limit: Maximum requests allowed in the window.
            window_seconds: The sliding window size in seconds.
            
        Returns:
            True if request is allowed, False if rate limited.
        """
        now = time.time()
        cutoff = now - window_seconds

        # Use Redis if available and online
        if self.redis_client is not None:
            try:
                r = self.redis_client.sync_client
                key = f"rate_limit:{client_id}"
                
                # Remove older scores outside our sliding window
                r.zremrangebyscore(key, 0, cutoff)
                
                # Count current elements in window
                current_requests = r.zcard(key)
                if current_requests >= limit:
                    logger.debug(f"[RateLimiter] Redis rate limit hit for {client_id} ({current_requests}/{limit})")
                    return False
                
                # Register current request
                r.zadd(key, {str(now): now})
                r.expire(key, window_seconds)
                return True
                
            except Exception as e:
                logger.warning(f"[RateLimiter] Redis command failed ({e}). Falling back to SQLite rate limiter.")

        # Fallback SQLite sliding-window rate limiter
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 1. Clean up stale logs older than cutoff
                cursor.execute("DELETE FROM request_logs WHERE timestamp <= ?", (cutoff,))
                
                # 2. Count current logs in sliding window
                cursor.execute("SELECT COUNT(*) FROM request_logs WHERE client_id = ?", (client_id,))
                current_requests = cursor.fetchone()[0]
                
                if current_requests >= limit:
                    logger.debug(f"[RateLimiter] SQLite rate limit hit for {client_id} ({current_requests}/{limit})")
                    return False
                
                # 3. Log current request
                cursor.execute("INSERT INTO request_logs (client_id, timestamp) VALUES (?, ?)", (client_id, now))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"[RateLimiter] SQLite rate limit check failed: {e}. Defaulting to allowing request.")
            return True
rate_limiter = RedisRateLimiter()