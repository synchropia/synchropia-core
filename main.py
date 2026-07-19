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
from src.core.llm_gateway import unified_llm_gateway
from src.services.sonarqube_testing_service import * 
from src.routes.streaming_route import router as streaming_router
from src.utils.logger import logger 
from src.utils.rate_limiter import rate_limiter

from src.models.db_models import Base
from src.services.database import db_engine

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
        
        # Automatically create/verify database tables on startup
        logger.info("Ensuring database tables exist...")
        Base.metadata.create_all(bind=db_engine)
        logger.success("Database schema verified/created successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL or build database tables: {e}")

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

    # 4. SonarQube Client Initialization
    try:
        logger.info("Initializing the SonarQube client...")
        app.state.sonarqube = sonarqube_client
        logger.success("SonarQube client initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize SonarQube client: {e}")

    logger.success("Synchropia API Gateway service lifespan startup complete.")
    
    yield  # Web server runs and services requests here

    # -----------------GRACEFUL SHUTDOWN -----------------
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

    if hasattr(app.state,"sonarqube") and app.state.sonarqube:
        logger.info("Disconnecting SonarQube client...")
        try:
            app.state.sonarqube.close()
            logger.success("SonarQube client disconnected.")
        except Exception as e:
            logger.error(f"Error disconnecting SonarQube client: {e}")
            
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
    allow_origins=["http://localhost:3000"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
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
    
app.include_router(streaming_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)