import aiohttp
import asyncio
from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_cerebras import ChatCerebras
from langchain_mistralai import ChatMistralAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_sambanova import ChatSambaNova

from src.config.settings import settings
from src.services.semantic_caching import semantic_cache
from src.utils.logger import logger
from src.services.kafka_service import kafka_client


@dataclass
class ProviderInfo:
    id: str
    name: str
    display_name: str
    base_url: str
    logo_url: str
    description: str
    requires_api_key: bool
    supports_streaming: bool
    supports_tools: bool
    supports_embeddings: bool
    max_tokens: Optional[int] = None


@dataclass
class ModelInfo:
    id: str
    name: str
    description: Optional[str] = None
    context_length: Optional[int] = None
    extra_info: Optional[Dict[str, Any]] = None


class BaseLLMProvider(ABC):
    """Base class for all LLM providers"""
    
    def __init__(self, provider_info: ProviderInfo, api_key: Optional[str] = None):
        self.provider_info = provider_info
        self.api_key = api_key
        self.base_url = provider_info.base_url
        
    @abstractmethod
    async def get_models(self) -> List[ModelInfo]:
        """Fetch available models from the provider"""
        pass

    @abstractmethod
    async def generate(self, model: str, prompt: str, **kwargs) -> str:
        """Generate a response text for a given prompt"""
        pass




class OpenAICompatibleProvider(BaseLLMProvider):
    """Base class for OpenAI-compatible providers (Groq, Mistral, Cerebras, OpenRouter, etc.)"""
    
    async def generate(self, model: str, prompt: str, **kwargs) -> str:
        if not self.api_key and self.provider_info.requires_api_key:
            logger.error(f"API key is not configured for {self.provider_info.display_name}.")
            raise ValueError(f"API key is not configured for {self.provider_info.display_name}.")
              
        provider_id = self.provider_info.id
        
        # Instantiate correct LangChain class based on provider
        match provider_id:
            case "openrouter":
                llm = ChatOpenAI(
                    model=model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.9),
                    max_tokens=kwargs.get("max_tokens", 100000),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "google":
                llm = ChatGoogleGenerativeAI(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.9),
                    max_tokens=kwargs.get("max_tokens", 100000),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "groq":
                llm = ChatGroq(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.9),
                    max_tokens=kwargs.get("max_tokens", 100000),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "mistral":
                llm = ChatMistralAI(
                    model=model,
                    api_key=self.api_key,
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.9),
                max_tokens=kwargs.get("max_tokens", 100000),
                stop=kwargs.get("stop", None),
                streaming=True
            )
        
            case "cerebras":
                llm = ChatCerebras(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.9),
                    max_tokens=kwargs.get("max_tokens", 100000),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "nvidia":
                llm = ChatNVIDIA(
                model=model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.9),
                max_tokens=kwargs.get("max_tokens", 100000),
                stop=kwargs.get("stop", None),
                streaming=True
            )
            case "sambanova":
                llm = ChatSambaNova(
                model=model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.9),
                max_tokens=kwargs.get("max_tokens", 100000),
                stop=kwargs.get("stop", None),
                streaming=True
            )
            case _:
                # Fallback to ChatOpenAI for any other OpenAI-compatible provider
                llm = ChatOpenAI(
                    model=model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.9),
                    max_tokens=kwargs.get("max_tokens", 100000),
                    stop=kwargs.get("stop", None),
                    streaming=True
            )

        logger.info(f"Generating content using {self.provider_info.display_name} model: {model}")
        try:
            run_id = kwargs.get("run_id", "")
            
            full_response = []
            async for chunk in llm.astream(prompt):
                token = chunk.content
                if token:
                    full_response.append(token)
                    event = {
                        "event": "token",
                        "source": provider_id,
                        "token": token,
                        "run_id": run_id
                    }
                    try:
                        await kafka_client.publish("synchropia-agent-responses", event)
                    except Exception as ke:
                        logger.warning(f"Failed to publish token to Kafka: {ke}")
            
            final_text = "".join(full_response)
            end_event = {
                "event": "agent_response",
                "source": provider_id,
                "text": final_text,
                "run_id": run_id
            }
            try:
                await kafka_client.publish("synchropia-agent-responses", end_event)
            except Exception as ke:
                logger.warning(f"Failed to publish final response to Kafka: {ke}")
                
            logger.success(f"Successfully generated response from {self.provider_info.display_name} model: {model}")
            return final_text
        except Exception as e:
            logger.error(f"Exception during generate for {self.provider_info.display_name} model {model}: {e}")
            raise


class GoogleAIProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        if not self.api_key:
            logger.warning("API key not configured for Google. Cannot fetch models.")
            return []
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"x-goog-api-key": self.api_key}
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching Google models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    models_list = data.get("models", [])
                    
                    return [
                        ModelInfo(
                            id=model.get("name", ""),
                            name=model.get("displayName") or model.get("name", ""),  
                            description=model.get("description"), 
                            context_length=model.get("inputTokenLimit") or 4096
                        )
                        for model in models_list
                        if model.get("name")  
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for Google: {e}")
            return []


class GroqProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        if not self.api_key:
            logger.warning("API key not configured for Groq. Cannot fetch models.")
            return []
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching Groq models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", ""),
                            context_length=model.get("context_window", 4096)
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for Groq: {e}")
            return []


class MistralProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        if not self.api_key:
            logger.warning("API key not configured for Mistral. Cannot fetch models.")
            return []
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching Mistral models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", ""),
                            context_length=model.get("max_context_length", 4096),
                            description=model.get("description", "")
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for Mistral: {e}")
            return []
    

class OpenRouterProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        if not self.api_key:
            logger.warning("API key not configured for OpenRouter. Cannot fetch models.")
            return []
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching OpenRouter models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("name") or model.get("id", ""),
                            description=model.get("description", ""),
                            context_length=model.get("context_length", 4096),
                            extra_info={k: v for k, v in model.items() if k not in ["id", "name", "description", "context_length"]}
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for OpenRouter: {e}")
            return []


class CerebrasProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        if not self.api_key:
            logger.warning("API key not configured for Cerebras. Cannot fetch models.")
            return []
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching Cerebras models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", "")
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for Cerebras: {e}")
            return []


class NvidiaNimProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching NVIDIA models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", "")
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for NVIDIA: {e}")
            return []


class SamanovaProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/models", headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Error fetching SambaNova models: HTTP {response.status} - {text}")
                        return []
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", ""),
                            context_length=model.get("context_length", 4096)
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.error(f"Exception during get_models for SambaNova: {e}")
            return []


class UnifiedLLMGateway:
    """Main router for managing LLM providers and models"""
    
    def __init__(self):
        self.providers: Dict[str, BaseLLMProvider] = {}
        self._initialize_providers()
    
    def _initialize_providers(self):
        """Initialize all available providers"""
        logger.info("Initializing UnifiedLLMGateway providers...")
        # Google Gemini
        google_info = ProviderInfo(
            id="google",
            name="google",
            display_name="Google",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            logo_url="https://registry.npmmirror.com/@lobehub/icons-static-png/latest/files/dark/gemini-color.png",
            description="Google's Gemini models",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=True
        )
        self.providers["google"] = GoogleAIProvider(google_info, settings.GOOGLE_API_KEY)
       
        # Groq
        groq_info = ProviderInfo(
            id="groq",
            name="groq",
            display_name="Groq",
            base_url="https://api.groq.com/openai/v1",
            logo_url="https://cdn.brandfetch.io/idxygbEPCQ/w/201/h/201/theme/dark/icon.png?c=1bxid64Mup7aczewSAYMX&t=1668515712972",
            description="Fast inference with Groq",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["groq"] = GroqProvider(groq_info, settings.GROQ_API_KEY)
        
        # Mistral
        mistral_info = ProviderInfo(
            id="mistral",
            name="mistral",
            display_name="Mistral AI",
            base_url="https://api.mistral.ai/v1",
            logo_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQfBpAyt03guidOXPIaR3o28eNlVqemSOjQEg&s",
            description="Mistral's efficient models",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["mistral"] = MistralProvider(mistral_info, settings.MISTRAL_API_KEY)
        
        # Cerebras
        cerebras_info = ProviderInfo(
            id="cerebras",
            name="cerebras",
            display_name="Cerebras",
            base_url="https://api.cerebras.ai/v1",
            logo_url="https://registry.npmmirror.com/@lobehub/icons-static-png/latest/files/dark/cerebras-color.png",
            description="Cerebras high-performance models",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["cerebras"] = CerebrasProvider(cerebras_info, settings.CEREBRAS_API_KEY)

        # NVIDIA NIM
        nvidia_nim_info = ProviderInfo(
            id="nvidia",
            name="nvidia",
            display_name="NVIDIA NIM",
            base_url="https://integrate.api.nvidia.com/v1",
            logo_url="https://developer-blogs.nvidia.com/wp-content/uploads/2024/03/nim-inference-microservices-1024x576.png",
            description="NVIDIA NIM models",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["nvidia"] = NvidiaNimProvider(nvidia_nim_info, settings.NVIDIA_API_KEY)

        # SambaNova
        sambanova_info = ProviderInfo(
            id="sambanova",
            name="sambanova",
            display_name="Sambanova",
            base_url="https://api.sambanova.ai/v1",
            logo_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRBdHN5aZkfTgTr3F1CeJjgLG5jVHElmPatfA&s",
            description="Sambanova models",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["sambanova"] = SamanovaProvider(sambanova_info, settings.SAMBANOVA_API_KEY)

        # OpenRouter
        openrouter_info = ProviderInfo(
            id="openrouter",
            name="openrouter",
            display_name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            logo_url="https://openrouter.ai/favicon.ico",
            description="Access any AI model",
            requires_api_key=True,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["openrouter"] = OpenRouterProvider(openrouter_info, settings.OPENROUTER_API_KEY)
        logger.success("UnifiedLLMGateway providers initialized successfully.")

    def get_provider(self, provider_id: str) -> Optional[BaseLLMProvider]:
        """Get a provider by ID"""
        return self.providers.get(provider_id)
    
    async def get_all_providers(self) -> List[ProviderInfo]:
        """Get all available providers"""
        return [provider.provider_info for provider in self.providers.values()]
    
    async def get_models_for_provider(self, provider_id: str) -> List[ModelInfo]:
        """Get models for a specific provider"""
        provider = self.get_provider(provider_id)
        if provider:
            return await provider.get_models()
        return []

    async def generate_response(self, provider_id: str, model_id: str, prompt: str, **kwargs) -> str:
        """Generate response using the specified provider, wrapping with semantic cache lookup and update."""
        logger.info(f"Gateway request received: provider='{provider_id}', model='{model_id}'")
        
        # 1. Lookup in semantic cache
        try:
            cached_res = await semantic_cache.lookup(prompt)
            if cached_res is not None:
                logger.success("Gateway request satisfied from semantic cache.")
                run_id = kwargs.get("run_id")
                if run_id:
                    import asyncio
                    words = cached_res.split(" ")
                    for i, word in enumerate(words):
                        # Restore the space between words
                        token = word + (" " if i < len(words) - 1 else "")
                        token_event = {
                            "event": "token",
                            "source": provider_id,
                            "token": token,
                            "run_id": run_id,
                            "cached": True
                        }
                        try:
                            await kafka_client.publish("synchropia-agent-responses", token_event)
                        except Exception as ke:
                            logger.warning(f"Failed to publish cached token to Kafka: {ke}")
                        await asyncio.sleep(0.015)
                        
                    response_event = {
                        "event": "agent_response",
                        "source": provider_id,
                        "text": cached_res,
                        "run_id": run_id,
                        "cached": True
                    }
                    try:
                        await kafka_client.publish("synchropia-agent-responses", response_event)
                    except Exception as ke:
                        logger.warning(f"Failed to publish cached response to Kafka: {ke}")
                return cached_res
        except Exception as e:
            logger.warning(f"Failed to lookup in semantic cache: {e}")

        # 2. Get provider
        provider = self.get_provider(provider_id)
        if not provider:
            logger.error(f"Provider '{provider_id}' is not supported or not configured.")
            raise ValueError(f"Provider '{provider_id}' is not supported or not configured.")

        # 3. Generate response from provider API
        try:
            response_text = await provider.generate(model_id, prompt, **kwargs)
        except Exception as e:
            logger.error(f"Failed to generate response from provider '{provider_id}' using model '{model_id}': {e}")
            raise

        # 4. Save response to semantic cache
        try:
            await semantic_cache.set(prompt, response_text)
            logger.success("Successfully saved generated response to semantic cache.")
        except Exception as e:
            logger.warning(f"Failed to save response to semantic cache: {e}")

        return response_text

unified_llm_gateway = UnifiedLLMGateway()