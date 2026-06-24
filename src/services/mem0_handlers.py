import os
import httpx
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from src.utils.logger import logger  

load_dotenv()

class MemoryLayer:
    """Memory Layer Client for the Mem0 Platform API (V3/V1).
    
    Provides async operations to add, retrieve, search, update, and delete
    memories on the Mem0 service.
    
    References Mem0 V3 Additive Pipeline and V1 endpoints.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.mem0.ai"
    ):
        self.api_key = api_key or os.getenv("MEM0_API_KEY")
        self.base_url = base_url.rstrip("/")
        
        if not self.api_key:
            logger.warning(
                "MEM0_API_KEY is not set. Mem0 API calls will fail unless "
                "an API key is passed explicitly to this client or set in the environment."
            )
            
        # Configure headers according to Mem0 authorization specs
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"
            
        # Persistent async client for connection pooling
        self.client = httpx.AsyncClient(headers=headers, timeout=30.0)
        logger.info(f"MemoryLayer client initialized for base URL: {self.base_url}")

    async def aclose(self):
        """Clean up the async client connection pool."""
        await self.client.aclose()
        logger.info("MemoryLayer client connection pool closed.")

    async def add_memories(
        self,
        user_id: str,
        messages: List[Dict[str, str]],
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True
    ) -> Dict[str, Any]:
        """Extract and store memories from a conversation using the V3 additive pipeline.
        
        Memories accumulate over time with single-pass ADD-only extraction.
        """
        url = f"{self.base_url}/v3/memories/add/"
        payload = {
            "user_id": user_id,
            "messages": messages,
            "metadata": metadata or {},
            "infer": infer
        }
        
        logger.info(f"Mem0 Add: Sending additive memory request for user_id: '{user_id}'...")
        response = await self.client.post(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        logger.success(f"Mem0 Add: Successfully added/updated memories for user_id: '{user_id}'.")
        return data

    async def get_memories(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 50,
        filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List memories scoped by filters with paginated results.
        
        The user_id filter is passed automatically inside the filters object.
        """
        url = f"{self.base_url}/v3/memories/"
        
        query_filters = {"user_id": user_id}
        if filters:
            query_filters.update(filters)
            
        payload = {
            "filters": query_filters
        }
        params = {
            "page": page,
            "page_size": page_size
        }
        
        logger.info(f"Mem0 Get: Retrieving memories for user_id: '{user_id}' (Page: {page}, Page size: {page_size})...")
        response = await self.client.post(url, json=payload, params=params)
        response.raise_for_status()
        
        data = response.json()
        logger.success(f"Mem0 Get: Successfully retrieved {len(data.get('results', []))} memories.")
        return data

    async def search_memories(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        threshold: float = 0.1,
        rerank: bool = False,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Search memories using relevance-ranked hybrid search (semantic + BM25 + entity matching)."""
        url = f"{self.base_url}/v3/memories/search/"
        
        query_filters = {"user_id": user_id}
        if filters:
            query_filters.update(filters)
            
        payload = {
            "query": query,
            "filters": query_filters,
            "top_k": top_k,
            "threshold": threshold,
            "rerank": rerank
        }
        
        logger.info(f"Mem0 Search: Running query '{query[:40]}' on user_id: '{user_id}'...")
        response = await self.client.post(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        logger.success(f"Mem0 Search: Found {len(data)} matching memory records.")
        return data

    async def update_memory(
        self,
        memory_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Update the content or metadata of a single memory by its unique ID (V1 endpoint)."""
        url = f"{self.base_url}/v1/memories/{memory_id}/"
        payload = {
            "text": text,
            "metadata": metadata or {}
        }
        
        logger.info(f"Mem0 Update: Modifying memory record '{memory_id}'...")
        response = await self.client.put(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        logger.success(f"Mem0 Update: Memory record '{memory_id}' successfully updated.")
        return data

    async def delete_memory(
        self,
        memory_id: str
    ) -> Dict[str, Any]:
        """Delete a single memory by its unique memory ID (V1 endpoint)."""
        url = f"{self.base_url}/v1/memories/{memory_id}/"
        
        logger.info(f"Mem0 Delete: Removing memory record '{memory_id}'...")
        response = await self.client.delete(url)
        response.raise_for_status()
        
        data = response.json()
        logger.success(f"Mem0 Delete: Memory record '{memory_id}' successfully removed.")
        return data


# Verification self-test block
if __name__ == "__main__":
    import asyncio

    async def test_run():
        logger.info("Initializing direct MemoryLayer verification run...")
        # Instantiating client (reads MEM0_API_KEY from environment)
        client = MemoryLayer()
        
        try:
            # We only run the test if the API key is present
            if not client.api_key or client.api_key == "your-api-key-here":
                logger.warning("Skipping live verification tests. MEM0_API_KEY is not set or is set to default.")
                return
                
            # Perform a test search query (non-destructive)
            res = await client.search_memories(
                query="Does the user have a database preference?",
                user_id="test_user_synchropia",
                top_k=2
            )
            print("Search Results:", res)
            
        except Exception as e:
            logger.error(f"MemoryLayer verification encountered an error: {e}")
        finally:
            await client.aclose()

    asyncio.run(test_run())