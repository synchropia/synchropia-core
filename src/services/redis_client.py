import os
from typing import Any, Optional, Dict, Union
import redis
import redis.asyncio as async_redis
from loguru import logger
from dotenv import load_dotenv
from src.config.settings import settings

load_dotenv()

class RedisAPIClient:
    """A comprehensive client for interacting with Redis.
    
    Provides both synchronous and asynchronous operations using persistent
    connection pooling to optimize TCP handshake overhead.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: Optional[int] = None,
        password: Optional[str] = None,
    ):
        # Read from settings if not supplied
        self.host = host or settings.REDIS_HOST
        self.port = port or settings.REDIS_PORT
        self.db = db or settings.REDIS_DB
        self.password = password or settings.REDIS_PASSWORD

        logger.info(f"Initializing Redis connection pool to {self.host}:{self.port} (DB {self.db})")

        # Synchronous Connection Pool & Client
        self.sync_pool = redis.ConnectionPool(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
            socket_timeout=5.0
        )
        self.sync_client = redis.Redis(connection_pool=self.sync_pool)

        # Asynchronous Connection Pool & Client
        self.async_pool = async_redis.ConnectionPool(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
            socket_timeout=5.0
        )
        self.async_client = async_redis.Redis(connection_pool=self.async_pool)
        
        logger.success("Redis connection pools (sync & async) initialized successfully.")

    def __del__(self):
        """Clean up synchronous connection pool on garbage collection."""
        try:
            self.sync_pool.disconnect()
        except Exception:
            pass

    async def aclose(self):
        """Clean up asynchronous connection pool."""
        try:
            await self.async_pool.disconnect()
        except Exception:
            pass

    # --- Synchronous Operations ---

    def get(self, key: str) -> Optional[str]:
        """Retrieve key value synchronously."""
        logger.debug(f"Redis GET: {key}")
        try:
            return self.sync_client.get(key)
        except Exception as e:
            logger.error(f"Redis GET failed for key {key}: {e}")
            return None

    def set(self, key: str, value: Union[str, int, float], ex: Optional[int] = None) -> bool:
        """Store key value synchronously, optionally with expiration in seconds."""
        logger.debug(f"Redis SET: {key} = {value} (ex={ex})")
        try:
            return bool(self.sync_client.set(key, value, ex=ex))
        except Exception as e:
            logger.error(f"Redis SET failed for key {key}: {e}")
            return False

    def delete(self, key: str) -> int:
        """Delete a key synchronously."""
        logger.debug(f"Redis DEL: {key}")
        try:
            return self.sync_client.delete(key)
        except Exception as e:
            logger.error(f"Redis DEL failed for key {key}: {e}")
            return 0

    def publish(self, channel: str, message: str) -> int:
        """Publish a message to a pub/sub channel synchronously."""
        logger.debug(f"Redis PUBLISH to channel '{channel}'")
        try:
            return self.sync_client.publish(channel, message)
        except Exception as e:
            logger.error(f"Redis PUBLISH failed on channel {channel}: {e}")
            return 0

    # --- Asynchronous Operations ---

    async def aget(self, key: str) -> Optional[str]:
        """Retrieve key value asynchronously."""
        logger.debug(f"Redis async GET: {key}")
        try:
            return await self.async_client.get(key)
        except Exception as e:
            logger.error(f"Redis async GET failed for key {key}: {e}")
            return None

    async def aset(self, key: str, value: Union[str, int, float], ex: Optional[int] = None) -> bool:
        """Store key value asynchronously, optionally with expiration in seconds."""
        logger.debug(f"Redis async SET: {key} = {value} (ex={ex})")
        try:
            return bool(await self.async_client.set(key, value, ex=ex))
        except Exception as e:
            logger.error(f"Redis async SET failed for key {key}: {e}")
            return False

    async def adelete(self, key: str) -> int:
        """Delete a key asynchronously."""
        logger.debug(f"Redis async DEL: {key}")
        try:
            return await self.async_client.delete(key)
        except Exception as e:
            logger.error(f"Redis async DEL failed for key {key}: {e}")
            return 0

    async def apublish(self, channel: str, message: str) -> int:
        """Publish a message to a pub/sub channel asynchronously."""
        logger.debug(f"Redis async PUBLISH to channel '{channel}'")
        try:
            return await self.async_client.publish(channel, message)
        except Exception as e:
            logger.error(f"Redis async PUBLISH failed on channel {channel}: {e}")
            return 0

if __name__ == "__main__":
    redis_client = RedisAPIClient()
    redis_client.set("test", "test")
    print(redis_client.get("test"))
    redis_client.delete("test")
    print(redis_client.get("test"))