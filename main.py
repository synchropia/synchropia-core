import uvicorn
import json
import uuid
import asyncio
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, APIRouter
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import create_engine

from src.deps.auth import get_current_user, create_access_token
from src.config.settings import settings
from src.services.redis_client import redis_client
from src.services.kafka_service import kafka_client
from src.core.llm_gateway import UnifiedLLMGateway
from src.agents.architect.index import run_architect

# Database engine initialization
db_engine = create_engine(str(settings.DATABASE_URL_DEV))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ------------------ STARTUP ------------------
    logger.info("Starting up Synchropia API Gateway service lifespan...")

    # 1. PostgreSQL Database Connection Check
    try:
        logger.info("Verifying PostgreSQL database connectivity...")
        with db_engine.connect() as connection:
            logger.success("PostgreSQL connection verified successfully.")
            app.state.db = db_engine
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL database: {e}")

    # 2. Redis Connection Check
    try:
        logger.info("Verifying Redis connectivity...")
        if redis_client.sync_client.ping():
            logger.success("Redis connection verified successfully.")
            app.state.redis = redis_client
    except Exception as e:
        logger.error(f"Failed to connect to Redis server: {e}")

    # 3. Kafka Connection Check
    try:
        logger.info("Verifying Kafka connection...")
        await kafka_client.start_producer()
        logger.success("Kafka connection verified successfully.")
        app.state.kafka = kafka_client
    except Exception as e:
        logger.error(f"Failed to start Kafka producer connection: {e}")

    logger.success("Synchropia API Gateway service lifespan startup complete.")
    
    yield  # Web server runs and services requests here
    
    # ----------------- SHUTDOWN -----------------
    logger.info("Shutting down Synchropia API Gateway service lifespan...")

    # 1. Shutdown Kafka producer
    if hasattr(app.state, "kafka") and app.state.kafka:
        logger.info("Stopping Kafka client...")
        try:
            await app.state.kafka.stop_producer()
            logger.success("Kafka client stopped.")
        except Exception as e:
            logger.error(f"Error stopping Kafka client: {e}")

    # 2. Shutdown Redis client
    if hasattr(app.state, "redis") and app.state.redis:
        logger.info("Disconnecting Redis client...")
        try:
            await app.state.redis.aclose()
            logger.success("Redis client disconnected.")
        except Exception as e:
            logger.error(f"Error disconnecting Redis client: {e}")

    logger.success("Lifespan shutdown complete.")

# Create the main FastAPI application with lifespan
app = FastAPI(
    title="Synchropia API Gateway",
    description="Swarm Orchestration and Agentic Incident Management API",
    version="0.1.0",
    lifespan=lifespan,
)

# Set up CORS middleware for premium UI integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. PUBLIC ROUTES (e.g. Health Check)
public_router = APIRouter()

@public_router.get("/health", tags=["System"])
def health_check():
    """
    Public endpoint to check database and service health.
    """
    return {
        "status": "healthy",
        "services": {
            "database": "connected",
            "redis": "connected",
            "kafka": "running"
        }
    }

@public_router.post("/token", tags=["Authentication"])
def generate_token(username: str):
    """
    Convenience endpoint to issue mock JWT tokens for testing API endpoints.
    """
    token = create_access_token(data={"sub": username})
    return {"access_token": token, "token_type": "bearer"}

class ChatStreamRequest(BaseModel):
    prompt: str
    provider: str 
    model: str 
    temperature: float = 0.7
    max_tokens: int = 1024

llm_gateway = UnifiedLLMGateway()

@public_router.post("/api/v1/chat/stream", tags=["Chat"])
async def chat_stream(req: ChatStreamRequest):
    """
    Server-Sent Events (SSE) endpoint that triggers async Architect agent generation
    and streams tokens/events from Kafka.
    """
    run_id = str(uuid.uuid4())
    
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
            run_architect(
                message=req.prompt,
                provider=req.provider,
                run_id=run_id,
            )
        )

        
        try:
            yield f"data: {json.dumps({'event': 'connected', 'run_id': run_id})}\n\n"
            
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


protected_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)]
)

@protected_router.get("/user/me", tags=["Authentication"])
def get_me(current_user: dict = Depends(get_current_user)):
    """
    Returns the parsed JWT payload of the authenticated user.
    """
    return {
        "user_id": current_user.get("sub"),
        "claims": current_user
    }

@protected_router.get("/agents", tags=["Agents"])
def list_agents():
    """
    List all active agents in the Synchropia swarm.
    """
    return {
        "agents": [
            {"id": "pm", "name": "PM Agent", "role": "Orchestrator"},
            {"id": "architect", "name": "Architect Agent", "role": "System Blueprinting"},
            {"id": "data_engineer", "name": "Data Engineer Agent", "role": "ETL/ELT Flows"},
            {"id": "ai_engineer", "name": "AI Engineer Agent", "role": "LLM Orchestration"},
        ]
    }

# Include routers in the main application
app.include_router(public_router)
app.include_router(protected_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
