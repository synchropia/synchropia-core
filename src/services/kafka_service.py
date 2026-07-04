import os
import json
import uuid
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
import asyncio
from typing import Any, Dict, List, Optional, Union
from loguru import logger
from dotenv import load_dotenv

# LangChain callbacks imports
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from src.config.settings import settings
from src.models.event_schemas import SwarmEvent, EventType
from src.services.database import get_db_session
from src.models.db_models import AgentExecution, ToolCall

load_dotenv()

class KafkaAPIClient:
    """A connection-pooled wrapper client for Kafka.
    
    Interacts directly with a real Kafka broker via aiokafka.
    """
    def __init__(self, bootstrap_servers: Optional[str] = None):
        self.bootstrap_servers = bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS
        self._producers: Dict[asyncio.AbstractEventLoop, AIOKafkaProducer] = {}

    @staticmethod
    def _json_serializer(v: Any) -> bytes:
        def default_encoder(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            if isinstance(obj, uuid.UUID):
                return str(obj)
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
        return json.dumps(v, default=default_encoder).encode("utf-8")

    async def get_producer(self, loop: asyncio.AbstractEventLoop) -> AIOKafkaProducer:
        """Get or initialize the producer bound to the specified event loop."""
        if loop not in self._producers or self._producers[loop] is None:
            logger.info(f"Initializing AIOKafkaProducer for event loop {id(loop)}")
            producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=self._json_serializer
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

    def __init__(self, kafka_client: KafkaAPIClient, default_agent_name: str = "Agent", session_id: str | None = None):
        super().__init__()
        self.kafka_client = kafka_client
        self.default_agent_name = default_agent_name
        self.run_id: str | None = None
        self.session_id: str | None = session_id
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def _get_agent_name(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        if metadata and "agent_name" in metadata:
            return metadata["agent_name"]
        return self.default_agent_name

    def _get_run_id(self, run_id: Any) -> str:
        """Resolve the parent agent run ID if configured, else fallback to the step-level run ID."""
        if self.run_id:
            return str(self.run_id)
        return str(run_id) if run_id else ""

    def _create_event(
        self, 
        event_type: EventType, 
        source_agent: str, 
        run_id: Any, 
        payload: dict = None, 
        error_details: str = None
    ) -> dict:
        """Helper to construct, validate and serialize a SwarmEvent."""
        resolved_run_id = self._get_run_id(run_id)
        event_obj = SwarmEvent(
            event_id=str(uuid.uuid4()),
            run_id=resolved_run_id,
            session_id=self.session_id or "default_session",
            event_type=event_type,
            source_agent=source_agent,
            payload=payload or {},
            error_details=error_details
        )
        
        # Conforming to SwarmEvent dict but maintaining top-level compatibility
        # for older event consumers by flattening payload & adding alias fields.
        data = event_obj.model_dump()
        data["event"] = event_type.value
        if payload:
            for k, v in payload.items():
                data[k] = v
        if error_details:
            data["error"] = error_details
            data["message"] = error_details
        return data

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

    def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: Optional[Any] = None,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish individual generated tokens for real-time streaming."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        token_str = token if isinstance(token, str) else json.dumps(token)
        event = self._create_event(
            event_type=EventType.TOKEN,
            source_agent=agent_name,
            run_id=run_id,
            payload={"token": token_str}
        )
        self._safe_publish("synchropia-agent-responses", event)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish the final generation text of the agent."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        for generation_list in response.generations:
            for generation in generation_list:
                event = self._create_event(
                    event_type=EventType.AGENT_RESPONSE,
                    source_agent=agent_name,
                    run_id=run_id,
                    payload={"text": generation.text}
                )
                self._safe_publish("synchropia-agent-responses", event)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish LLM error events."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = self._create_event(
            event_type=EventType.AGENT_ERROR,
            source_agent=agent_name,
            run_id=run_id,
            error_details=str(error)
        )
        self._safe_publish("synchropia-agent-responses", event)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish tool call activation details."""
        meta = metadata or kwargs.get("metadata")
        agent_name = self._get_agent_name(meta)
        tool_name = serialized.get("name", "unknown_tool")
        event = self._create_event(
            event_type=EventType.TOOL_CALL,
            source_agent=agent_name,
            run_id=run_id,
            payload={"tool": tool_name, "input": input_str}
        )
        self._safe_publish("synchropia-tool-calls", event)

        # Log ToolCall start state to Database
        try:
            try:
                args = json.loads(input_str)
            except Exception:
                args = {"input": input_str}

            with get_db_session() as db:
                resolved_run_id = self._get_run_id(run_id)
                db_exec = db.query(AgentExecution).filter_by(
                    run_id=resolved_run_id,
                    agent_name=agent_name
                ).order_by(AgentExecution.created_at.desc()).first()

                if db_exec:
                    db_tool = ToolCall(
                        id=str(run_id),  # Match the callback's specific run_id
                        agent_execution_id=db_exec.id,
                        tool_name=tool_name,
                        arguments=args,
                        status="RUNNING"
                    )
                    db.add(db_tool)
                    db.commit()
        except Exception as e:
            logger.warning(f"Database logging failed for tool start: {e}")

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish tool execution outputs."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = self._create_event(
            event_type=EventType.TOOL_RESPONSE,
            source_agent=agent_name,
            run_id=run_id,
            payload={"output": str(output)}
        )
        self._safe_publish("synchropia-tool-responses", event)

        # Update ToolCall completion state in Database
        try:
            with get_db_session() as db:
                db_tool = db.query(ToolCall).filter_by(id=str(run_id)).first()
                if db_tool:
                    db_tool.response_payload = str(output)
                    db_tool.status = "SUCCESS"
                    db.commit()
        except Exception as e:
            logger.warning(f"Database logging failed for tool end: {e}")

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish tool execution errors."""
        agent_name = self._get_agent_name(kwargs.get("metadata"))
        event = self._create_event(
            event_type=EventType.TOOL_ERROR,
            source_agent=agent_name,
            run_id=run_id,
            error_details=str(error)
        )
        self._safe_publish("synchropia-tool-responses", event)

        # Update ToolCall error state in Database
        try:
            with get_db_session() as db:
                db_tool = db.query(ToolCall).filter_by(id=str(run_id)).first()
                if db_tool:
                    db_tool.response_payload = str(error)
                    db_tool.status = "ERROR"
                    db.commit()
        except Exception as e:
            logger.warning(f"Database logging failed for tool error: {e}")

    def on_chain_start(
        self,
        serialized: Dict[str, Any],
        inputs: Dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish lifecycle start event for root runnable/agent chain."""
        # Only publish for the root agent run
        if parent_run_id is None or str(run_id) == str(self.run_id):
            meta = metadata or kwargs.get("metadata")
            agent_name = self._get_agent_name(meta)
            event = self._create_event(
                event_type=EventType.AGENT_START,
                source_agent=agent_name,
                run_id=run_id
            )
            self._safe_publish("synchropia-agent-responses", event)

    def on_chain_end(
        self,
        outputs: Dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish lifecycle end event for root runnable/agent chain."""
        # Only publish for the root agent run
        if parent_run_id is None or str(run_id) == str(self.run_id):
            agent_name = self._get_agent_name(kwargs.get("metadata"))
            event = self._create_event(
                event_type=EventType.AGENT_END,
                source_agent=agent_name,
                run_id=run_id
            )
            self._safe_publish("synchropia-agent-responses", event)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Publish lifecycle error event for root runnable/agent chain."""
        # Only publish for the root agent run
        if parent_run_id is None or str(run_id) == str(self.run_id):
            agent_name = self._get_agent_name(kwargs.get("metadata"))
            event = self._create_event(
                event_type=EventType.AGENT_ERROR,
                source_agent=agent_name,
                run_id=run_id,
                error_details=str(error)
            )
            self._safe_publish("synchropia-agent-responses", event)

kafka_client = KafkaAPIClient()

__all__ = ["KafkaAPIClient", "KafkaAgentCallbackHandler", "kafka_client"]

if __name__ == "__main__":
    callback_handler = KafkaAgentCallbackHandler(kafka_client)
    
    