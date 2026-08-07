"""
routes/streaming_route.py
--------------------
FastAPI endpoints for streaming events and authenticating swarm agents.
"""

import json
import uuid
import asyncio
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from src.deps.auth import get_current_user
from src.services.kafka_service import kafka_client
from src.agents.orchestrator.dispatcher import run_swarm

router = APIRouter(prefix="/api/v1")

class ChatStreamRequest(BaseModel):
    prompt: str
    provider: str 
    model: str 
    temperature: float = 0.7
    max_tokens: int = 1024
    org: str
    org_id: str | None = None
    dir_path: str = ""
    thread_id: str | None = None
    resume: bool = False

@router.post("/chat/stream", tags=["Chat"])
async def chat_stream(
    req: ChatStreamRequest,
    dir_path: str | None = None,
    # current_user: dict = Depends(get_current_user)
):
    """
    Server-Sent Events (SSE) endpoint that triggers async swarm agent generation
    and streams tokens/events from Kafka.
    """
    run_id = str(uuid.uuid4())
    # user_id = current_user.get("sub") or "default_user"
    user_id = req.thread_id or str(uuid.uuid4())

    resolved_dir_path = dir_path or req.dir_path or None
    if resolved_dir_path == "":
        resolved_dir_path = None

    async def sse_event_generator():
        # 1. Consume from Kafka topics and yield events
        consumer = kafka_client.get_consumer(
            "synchropia-agent-responses",
            "synchropia-tool-calls",
            "synchropia-tool-responses"
        )
        await consumer.start()

        # 2. Start agent execution in a background task
        generation_task = asyncio.create_task(
            run_swarm(
                message=req.prompt,
                provider=req.provider,
                run_id=run_id,
                org=req.org,
                org_id=req.org_id,
                user_id=user_id,
                resume=req.resume,
                dir_path=resolved_dir_path
            )
        )

        try:
            yield f"data: {json.dumps({'event': 'connected', 'run_id': run_id, 'thread_id': user_id})}\n\n"
            
            while True:
                try:
                    msg = await asyncio.wait_for(consumer.getone(), timeout=0.5)
                    event = msg.value
                    if event.get("run_id") == run_id:
                        yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    if generation_task.done():
                        break
            
            # Drain any remaining buffered events that arrived right as the task finished
            while True:
                try:
                    msg = await asyncio.wait_for(consumer.getone(), timeout=0.1)
                    event = msg.value
                    if event.get("run_id") == run_id:
                        yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    break

            # Check if the task failed with an exception
            if generation_task.done():
                exc = generation_task.exception()
                if exc:
                    yield f"data: {json.dumps({'event': 'error', 'message': str(exc), 'run_id': run_id})}\n\n"

        finally:
            await consumer.stop()
            if not generation_task.done():
                generation_task.cancel()
                
    return StreamingResponse(sse_event_generator(), media_type="text/event-stream")
