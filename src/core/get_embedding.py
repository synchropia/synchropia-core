import os
import httpx
import asyncio
from typing import List, Optional
from dotenv import load_dotenv

from langchain_cohere import CohereEmbeddings
from src.config.settings import settings 

load_dotenv()

# class OpenRouterEmbeddings(Embeddings):
#     """Custom LangChain Embeddings class built on top of httpx
#     to fetch text embeddings from OpenRouter using persistent sync and async connection pools.
#     """

#     def __init__(
#         self,
#         model: str = "embed-v4.0",
#         openrouter_api_key: Optional[str] = None,
#         base_url: str = "https://api.cohere.com/v2/embed",
#         max_connections: int = 100,
#         max_keepalive_connections: int = 20,
#         timeout: float = 30.0
#     ):
#         self.model = model
#         self.openrouter_api_key = openrouter_api_key or settings.COHERE_API_KEY
#         self.base_url = base_url
        
#         if not self.openrouter_api_key:
#             raise ValueError(
#                 "COHERE_API_KEY must be provided to OpenRouterEmbeddings "
#                 "or set as an environment variable."
#             )

#         headers = {
#             "Authorization": f"Bearer {self.openrouter_api_key}",
#             "Content-Type": "application/json",
#         }
        
#         # Configure connection pooling limits in httpx
#         limits = httpx.Limits(
#             max_connections=max_connections,
#             max_keepalive_connections=max_keepalive_connections
#         )
        
#         # Initialize sync client (reuses connection pool)
#         self.client = httpx.Client(
#             headers=headers,
#             limits=limits,
#             timeout=timeout
#         )
        
#         # Initialize async client (reuses connection pool natively in event loop)
#         self.aclient = httpx.AsyncClient(
#             headers=headers,
#             limits=limits,
#             timeout=timeout
#         )

#     def __del__(self):
#         """Clean up the sync connection pool on destruction."""
#         try:
#             if hasattr(self, "client"):
#                 self.client.close()
#         except Exception:
#             pass

#     async def aclose(self):
#         """Clean up the async connection pool."""
#         if hasattr(self, "aclient"):
#             await self.aclient.aclose()

#     def close(self):
#         """Manually clean up the sync connection pool."""
#         if hasattr(self, "client"):
#             self.client.close()

#     def embed_documents(self, texts: List[str]) -> List[List[float]]:
#         """Embed a list of documents/texts using the persistent sync httpx client."""
#         if not texts:
#             return []
            
#         payload = {
#             "model": self.model,
#             "embedding_types": ["float"],
#             "embedding_types": ["float"],
#             "input": texts
#         }
        
#         response = self.client.post(self.base_url, json=payload)
#         response.raise_for_status()
        
#         data = response.json()
#         try:
#             return [item["embedding"] for item in data["data"]]
#         except (KeyError, IndexError, TypeError) as e:
#             raise ValueError(f"Failed to parse batched embeddings response from OpenRouter: {data}") from e

#     def embed_query(self, text: str) -> List[float]:
#         """Embed a single query string using the persistent sync httpx client."""
#         payload = {
#             "model": self.model,
#             "input": text
#         }
        
#         response = self.client.post(self.base_url, json=payload)
#         response.raise_for_status()
        
#         data = response.json()
#         try:
#             return data["data"][0]["embedding"]
#         except (KeyError, IndexError, TypeError) as e:
#             raise ValueError(f"Failed to parse embedding response from OpenRouter: {data}") from e

#     async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
#         """Asynchronously embed a list of documents using the persistent async httpx client."""
#         if not texts:
#             return []
            
#         payload = {
#             "model": self.model,
#             "input": texts
#         }
        
#         response = await self.aclient.post(self.base_url, json=payload)
#         response.raise_for_status()
        
#         data = response.json()
#         try:
#             return [item["embedding"] for item in data["data"]]
#         except (KeyError, IndexError, TypeError) as e:
#             raise ValueError(f"Failed to parse batched embeddings response from OpenRouter: {data}") from e

#     async def aembed_query(self, text: str) -> List[float]:
#         """Asynchronously embed a query prompt using the persistent async httpx client."""
#         payload = {
#             "model": self.model,
#             "input": text
#         }
        
#         response = await self.aclient.post(self.base_url, json=payload)
#         response.raise_for_status()
        
#         data = response.json()
#         try:
#             return data["data"][0]["embedding"]
#         except (KeyError, IndexError, TypeError) as e:
#             raise ValueError(f"Failed to parse embedding response from OpenRouter: {data}") from e



if __name__ == "__main__":
    import asyncio
    
    async def main():
        embedding = await embed.aembed_query("Hello, how are you?")
        print(embedding)
    asyncio.run(main())