import os
import json
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
import asyncio
from typing import Any, Dict, List, Optional, Union
from loguru import logger
from dotenv import load_dotenv

# LangChain callbacks imports
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from src.config.settings import settings

load_dotenv()

class KafkaAPIClient:
    """A connection-pooled wrapper client for Kafka.
    
    Interacts directly with a real Kafka broker via aiokafka.
    """
    def __init__(self, bootstrap_servers: Optional[str] = None):
        self.bootstrap_servers = bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS
        self._producers: Dict[asyncio.AbstractEventLoop, AIOKafkaProducer] = {}

    async def get_producer(self, loop: asyncio.AbstractEventLoop) -> AIOKafkaProducer:
        """Get or initialize the producer bound to the specified event loop."""
        if loop not in self._producers or self._producers[loop] is None:
            logger.info(f"Initializing AIOKafkaProducer for event loop {id(loop)}")
            producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
            # Short timeout to avoid hanging if broker is completely offline
            await asyncio.wait_for(producer.start(), timeout=5.0)
            self._producers[loop] = producer
            logger.success(f"Connected AIOKafkaProducer for event loop {id(loop)}")
        return self._producers[loop]

    async def start_producer(self):
        """Initialize and start the producer for the current running event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("No running event loop found during start_producer call.")
            raise RuntimeError("No running event loop found during start_producer call.")
        await self.get_producer(loop)


    async def stop_producer(self):
        """Clean up all active producers."""
        for loop, producer in list(self._producers.items()):
            try:
                if producer:
                    await producer.stop()
            except Exception as e:
                logger.warning(f"Error stopping producer for loop {id(loop)}: {e}")
        self._producers.clear()

    async def publish(self, topic: str, value: Any):
        """Publish an event value to a Kafka topic."""
        # Format message value as dict if not already
        if not isinstance(value, dict):
            value = {"data": str(value)}
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No running event loop found during publish fallback.")
            return

        try:
            producer = await self.get_producer(loop)
            await producer.send_and_wait(topic, value)
        except Exception as e:
            logger.error(f"Failed to publish message to topic {topic}: {e}")
            raise e

    def get_consumer(self, *topics: str) -> AIOKafkaConsumer:
        """Return a consumer subscribed to the specified topics."""
        try:
            consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self.bootstrap_servers,
                value_deserializer=lambda m: json.loads(m.decode("utf-8"))
            )
            return consumer
        except Exception as e:
            logger.error(f"Failed to create AIOKafkaConsumer: {e}")
            raise e

class KafkaAgentCallbackHandler(BaseCallbackHandler):
    """A custom LangChain callback handler that publishes agent and tool events to Kafka.
    
    Streams:
    - Tool calls (name and inputs)
    - Tool responses (outputs)
    - Textual responses (token streams and final output text)
    """

    def __init__(self, kafka_client: KafkaAPIClient, default_agent_name: str = "Agent"):
        super().__init__()
        self.kafka_client = kafka_client
        self.default_agent_name = default_agent_name
        self.run_id: str | None = None
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def _get_agent_name(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        if metadata and "agent_name" in metadata:
            return metadata["agent_name"]
        return self.default_agent_name

    def _get_run_id(self, kwargs: Any) -> str:
        """Resolve the parent agent run ID if configured, else fallback to the step-level run ID."""
        if self.run_id:
            return str(self.run_id)
        return str(kwargs.get("run_id", ""))

    def _safe_publish(self, topic: str, event: dict) -> None:
        """Publish an event to Kafka in a thread-safe, loop-safe manner."""
        # 1. Try sending via the captured main event loop (most reliable for cross-thread sync callbacks)
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self.kafka_client.publish(topic, event),
                    self.loop
                )
                return
            except Exception as e:
                logger.warning(f"Failed to publish via run_coroutine_threadsafe: {e}")

        # 2. Fallback to current thread's running loop if different or if main loop is not set/running
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.kafka_client.publish(topic, event))
                return
        except RuntimeError:
            pass

        # 3. Last resort fallback: run in a temporary loop or blocking call
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.kafka_client.publish(topic, event))
            else:
                loop.run_until_complete(self.kafka_client.publish(topic, event))
        except Exception as e:
            logger.error(f"Kafka fallback publish failed: {e}")

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Publish individual generated tokens for real-time streaming."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = {
            "event": "token",
            "source": agent_name,
            "token": token,
            "run_id": self._get_run_id(kwargs)
        }
        self._safe_publish("synchropia-agent-responses", event)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Publish the final generation text of the agent."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        for generation_list in response.generations:
            for generation in generation_list:
                event = {
                    "event": "agent_response",
                    "source": agent_name,
                    "text": generation.text,
                    "run_id": self._get_run_id(kwargs)
                }
                self._safe_publish("synchropia-agent-responses", event)

    def on_tool_start(
        self, serialized: Dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        """Publish tool call activation details."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        tool_name = serialized.get("name", "unknown_tool")
        event = {
            "event": "tool_call",
            "source": agent_name,
            "tool": tool_name,
            "input": input_str,
            "run_id": self._get_run_id(kwargs)
        }
        self._safe_publish("synchropia-tool-calls", event)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Publish tool execution outputs."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = {
            "event": "tool_response",
            "source": agent_name,
            "output": str(output),
            "run_id": self._get_run_id(kwargs)
        }
        self._safe_publish("synchropia-tool-responses", event)

kafka_client = KafkaAPIClient()

__all__ = ["KafkaAPIClient", "KafkaAgentCallbackHandler", "kafka_client"]
