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
        self.producer = None
        self.producer_started = False

    async def start_producer(self):
        """Initialize and start the producer."""
        try:
            logger.info(f"Attempting connection to Kafka bootstrap servers at {self.bootstrap_servers}")
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
            # Short timeout to avoid hanging if broker is completely offline
            await asyncio.wait_for(self.producer.start(), timeout=5.0)
            self.producer_started = True
            logger.success("Connected to Kafka broker successfully.")
        except Exception as e:
            logger.error(f"Could not connect to Kafka broker at {self.bootstrap_servers}: {e}")
            raise e

    async def stop_producer(self):
        """Clean up the producer."""
        if self.producer and self.producer_started:
            await self.producer.stop()
            self.producer_started = False

    async def publish(self, topic: str, value: Any):
        """Publish an event value to a Kafka topic."""
        if not self.producer_started:
            await self.start_producer()

        # Format message value as dict if not already
        if not isinstance(value, dict):
            value = {"data": str(value)}
        
        try:
            await self.producer.send_and_wait(topic, value)
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

    def _get_agent_name(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        if metadata and "agent_name" in metadata:
            return metadata["agent_name"]
        return self.default_agent_name

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Publish individual generated tokens for real-time streaming."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = {
            "event": "token",
            "source": agent_name,
            "token": token,
            "run_id": str(kwargs.get("run_id", ""))
        }
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.kafka_client.publish("synchropia-agent-responses", event))
        except RuntimeError:
            asyncio.run(self.kafka_client.publish("synchropia-agent-responses", event))

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Publish the final generation text of the agent."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        for generation_list in response.generations:
            for generation in generation_list:
                event = {
                    "event": "agent_response",
                    "source": agent_name,
                    "text": generation.text,
                    "run_id": str(kwargs.get("run_id", ""))
                }
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        loop.create_task(self.kafka_client.publish("synchropia-agent-responses", event))
                except RuntimeError:
                    asyncio.run(self.kafka_client.publish("synchropia-agent-responses", event))

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
            "run_id": str(kwargs.get("run_id", ""))
        }
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.kafka_client.publish("synchropia-tool-calls", event))
        except RuntimeError:
            asyncio.run(self.kafka_client.publish("synchropia-tool-calls", event))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Publish tool execution outputs."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = {
            "event": "tool_response",
            "source": agent_name,
            "output": str(output),
            "run_id": str(kwargs.get("run_id", ""))
        }
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.kafka_client.publish("synchropia-tool-responses", event))
        except RuntimeError:
            asyncio.run(self.kafka_client.publish("synchropia-tool-responses", event))

kafka_client = KafkaAPIClient()
