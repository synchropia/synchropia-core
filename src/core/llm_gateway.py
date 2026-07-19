# C:\Users\Raghu\Downloads\MAS_Agents\synchropia\src\core\llm_gateway.py

import aiohttp
import asyncio
from typing import Dict, List, Optional, Any,AsyncIterator
from abc import ABC, abstractmethod
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

import json
import asyncio
import requests
from typing import List, Optional, Any, Sequence, Union, Callable, Type, Dict
from pydantic import BaseModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage, AIMessageChunk, ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatResult, ChatGenerationChunk
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.output_parsers.openai_tools import parse_tool_calls


class ChatSelfHosted(BaseChatModel):
    """LangChain wrapper for a local OpenAI-compatible server with full tool support."""
    base_url: str = "http://127.0.0.1:8081"
    model_name: str = "Qwen3.5-4B"
    temperature: float = 0.1
    streaming: bool = False

    @property
    def _llm_type(self) -> str: return "Qwen3.5-4B"

    def _prepare_payload(self, messages: List[BaseMessage], **kwargs: Any) -> Dict[str, Any]:
        def extract_text(content: Any) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append(item.get("text") or "")
                return "".join(parts)
            return str(content)

        processed_messages = []
        for m in messages:
            m_content = extract_text(m.content)
            if isinstance(m, SystemMessage):
                processed_messages.append({"role": "system", "content": m_content})
            elif isinstance(m, HumanMessage):
                processed_messages.append({"role": "user", "content": m_content})
            elif isinstance(m, AIMessage):
                msg_dict = {"role": "assistant", "content": m_content or None}
                if m.tool_calls:
                    import uuid
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"])
                            }
                        }
                        for tc in m.tool_calls
                    ]
                processed_messages.append(msg_dict)
            elif isinstance(m, ToolMessage):
                processed_messages.append({
                    "role": "user",
                    "content": f"[Tool Output ({m.name or 'tool'}): {m_content}]"
                })
            else:
                raise TypeError(f"Unsupported message type: {type(m)}")
        payload = {"model": self.model_name, "messages": processed_messages, "temperature": self.temperature, **kwargs}
        if "tools" in payload and payload["tools"] is None:
            del payload["tools"]
        return payload

    def _send_request(self, messages: List[BaseMessage], **kwargs: Any) -> Dict[str, Any]:
        payload = self._prepare_payload(messages, **kwargs)
        print(f"DEBUG: Sending request to {self.base_url}/v1/chat/completions")
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions", 
                json=payload,
                timeout=60
            )
            print(f"DEBUG: Response Status: {response.status_code}")
            if response.status_code >= 400:
                print("ERROR PAYLOAD:", json.dumps(payload))
                print("ERROR RESPONSE:", response.text)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            print("ERROR: Request to LLM timed out after 60 seconds.")
            raise
        except Exception as e:
            print(f"ERROR: LLM Request failed: {str(e)}")
            raise

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
        resp_json = self._send_request(messages, **kwargs)
        message_data = resp_json["choices"][0]["message"]
        content = message_data.get("content", "") or ""
        reasoning_content = message_data.get("reasoning_content", "") or ""
        tool_calls = parse_tool_calls(message_data["tool_calls"]) if message_data.get("tool_calls") else []
        ai_message = AIMessage(
            content=content,
            tool_calls=tool_calls,
            additional_kwargs={"reasoning_content": reasoning_content} if reasoning_content else {}
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def bind_tools(self, tools: Sequence[Union[Dict, Type[BaseModel], Callable, BaseTool]], **kwargs: Any) -> Runnable:
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=formatted_tools, **kwargs)

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
        loop = asyncio.get_event_loop()
        resp_json = await loop.run_in_executor(None, lambda: self._send_request(messages, **kwargs))
        message_data = resp_json["choices"][0]["message"]
        content = message_data.get("content", "") or ""
        reasoning_content = message_data.get("reasoning_content", "") or ""
        tool_calls = parse_tool_calls(message_data["tool_calls"]) if message_data.get("tool_calls") else []
        ai_message = AIMessage(
            content=content,
            tool_calls=tool_calls,
            additional_kwargs={"reasoning_content": reasoning_content} if reasoning_content else {}
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        from typing import AsyncIterator
        import aiohttp

        payload = self._prepare_payload(messages, **kwargs)
        payload["stream"] = True
        url = f"{self.base_url}/v1/chat/completions"
        print(f"DEBUG: Streaming from {url}")

        yielded_chunks = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=300) as response:
                    if response.status >= 400:
                        text = await response.text()
                        print(f"ERROR STREAM RESPONSE: {response.status} - {text}")
                        response.raise_for_status()

                    async for line_bytes in response.content:
                        line = line_bytes.decode("utf-8").strip()
                        if not line:
                            continue
                        if line == "data: [DONE]":
                            break
                        if line.startswith("data: "):
                            data_str = line[len("data: "):]
                            try:
                                chunk_data = json.loads(data_str)
                                choice = chunk_data.get("choices", [{}])[0]
                                delta = choice.get("delta", {})
                                content = delta.get("content") or ""
                                reasoning_content = delta.get("reasoning_content") or ""
                                tool_calls = delta.get("tool_calls") or []

                                if content or tool_calls or reasoning_content:
                                    tc_chunks = []
                                    for tc in tool_calls:
                                        tc_chunks.append(
                                            ToolCallChunk(
                                                name=tc.get("function", {}).get("name"),
                                                args=tc.get("function", {}).get("arguments"),
                                                id=tc.get("id"),
                                                index=tc.get("index", 0)
                                            )
                                        )
                                    chunk_text = content
                                    message_chunk = AIMessageChunk(
                                        content=chunk_text, 
                                        tool_call_chunks=tc_chunks,
                                        additional_kwargs={"reasoning_content": reasoning_content} if reasoning_content else {}
                                    )
                                    generation_chunk = ChatGenerationChunk(message=message_chunk)
                                    yielded_chunks += 1
                                    yield generation_chunk
                            except Exception as ex:
                                logger.warning(f"Error parsing stream choice chunk: {ex}")
            if yielded_chunks == 0:
                logger.warning("Empty response stream from local model server. Emitting fallback chunk.")
                yield ChatGenerationChunk(message=AIMessageChunk(content="Swarm node execution completed."))
        except Exception as e:
            print(f"ERROR: LLM Stream failed: {str(e)}")
            raise

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


class OpenAICompatibleProvider(BaseLLMProvider):
    """Base class for OpenAI-compatible providers (Groq, Mistral, Cerebras, OpenRouter, etc.)"""
    
    def build_chat_module(self, model: str, **kwargs):
        if not self.api_key and self.provider_info.requires_api_key:
            logger.error(f"API key is not configured for {self.provider_info.display_name}.")
            raise ValueError(f"API key is not configured for {self.provider_info.display_name}.")
              
        provider_id = self.provider_info.id
        
        # Instantiate correct LangChain class based on provider with streaming enabled
        match provider_id:
            case "openrouter":
                llm = ChatOpenAI(
                    model=model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "google":
                llm = ChatGoogleGenerativeAI(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "groq":
                llm = ChatGroq(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "mistral":
                llm = ChatMistralAI(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "cerebras":
                llm = ChatCerebras(
                    model=model,
                    api_key=self.api_key,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "nvidia":
                llm = ChatNVIDIA(
                    model=model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "sambanova":
                llm = ChatSambaNova(
                    model=model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    temperature=kwargs.get("temperature", 0.7),
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
            case "selfhosted":
                llm = ChatSelfHosted(
                    base_url=self.base_url,
                    model_name=model,
                    temperature=kwargs.get("temperature", 0.7),
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
                    stop=kwargs.get("stop", None),
                    streaming=True
                )
        return llm


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

    async def generate(self, model: str, prompt: str, **kwargs) -> str:
        # Legacy fallback method
        llm = self.build_chat_module(model, **kwargs)
        logger.info(f"Generating content using direct fallback method: {model}")
        try:
            res = await llm.ainvoke(prompt)
            return res.content
        except Exception as e:
            logger.error(f"Exception during generation: {e}")
            raise


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


class SelfHostedProvider(OpenAICompatibleProvider):
    async def get_models(self) -> List[ModelInfo]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/v1/models") as response:
                    if response.status != 200:
                        logger.warning(f"Error fetching selfhosted models: HTTP {response.status}")
                        return [ModelInfo(id="Qwen3.5-4B", name="Qwen3.5-4B", context_length=8192)]
                    data = await response.json()
                    return [
                        ModelInfo(
                            id=model.get("id", ""),
                            name=model.get("id", ""),
                            context_length=model.get("context_length", 8192)
                        )
                        for model in data.get("data", [])
                    ]
        except Exception as e:
            logger.warning(f"Could not connect to selfhosted server at {self.base_url}: {e}. Returning default model info.")
            return [ModelInfo(id="Qwen3.5-4B", name="Qwen3.5-4B", context_length=8192)]


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

        # Self-Hosted
        selfhosted_info = ProviderInfo(
            id="selfhosted",
            name="selfhosted",
            display_name="Self-Hosted",
            base_url="http://127.0.0.1:8081",
            logo_url="https://github.com/vllm-project/vllm/raw/main/docs/source/assets/vllm-logo-text-light.png",
            description="Local self-hosted OpenAI-compatible LLM server",
            requires_api_key=False,
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False
        )
        self.providers["selfhosted"] = SelfHostedProvider(selfhosted_info, None)

        logger.success("UnifiedLLMGateway providers initialized successfully.")

    def get_provider(self, provider_id: str) -> Optional[BaseLLMProvider]:
        """Get a provider by ID"""
        return self.providers.get(provider_id)
    
    def get_chat_model(self, provider_id: str, model_id: str, **kwargs) -> Any:
        """Get the instantiated LangChain ChatModel instance configured for streaming."""
        provider = self.get_provider(provider_id)
        if not provider:
            logger.error(f"Provider '{provider_id}' is not supported.")
            raise ValueError(f"Provider '{provider_id}' is not supported.")
        
        if hasattr(provider, "build_chat_module"):
            return provider.build_chat_module(model_id, **kwargs)
        else:
            logger.error(f"Provider '{provider_id}' does not support chat module retrieval.")
            raise NotImplementedError(f"Provider '{provider_id}' does not support chat module retrieval.")

    async def get_all_providers(self) -> List[ProviderInfo]:
        """Get all available providers"""
        return [provider.provider_info for provider in self.providers.values()]
    
    async def get_models_for_provider(self, provider_id: str) -> List[ModelInfo]:
        """Get models for a specific provider"""
        provider = self.get_provider(provider_id)
        if provider:
            return await provider.get_models()
        return []


unified_llm_gateway = UnifiedLLMGateway()

