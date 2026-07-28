from typing import Optional
from pathlib import Path
from pydantic import PostgresDsn, Field 
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve absolute path to .env file relative to this config file
dotenv_path = Path(__file__).resolve().parents[2] / ".env"

class Settings(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_prefix="",
        env_file=dotenv_path,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # GitHub App Credentials
    GITHUB_APP_ID: str
    GITHUB_APP_PRIVATE_KEY_PATH: str
    GITHUB_INSTALLATION_ID: str
    
    # SonarQube credentials 
    SONARQUBE_API_KEY:str 
    SONARQUBE_BASE_URL: str = "https://sonarcloud.io"

    # Jira Integration Credentials
    JIRA_API_TOKEN: str
    JIRA_URL: str 
    JIRA_EMAIL: str 
    
    # Slack Integration Credentials
    SLACK_BOT_TOKEN: str 
    
    # PostgreSQL Configuration
    POSTGRES_HOST: str 
    POSTGRES_PORT: int 
    POSTGRES_USER: str 
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str 

    DATABASE_URL_DEV: PostgresDsn 
    
    # Redis Configuration
    REDIS_HOST: str 
    REDIS_PORT: int 
    REDIS_PASSWORD: str 
    REDIS_DB: int = 0
    
    # Kafka Configuration
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    
    # Cohere Embedding Model 
    COHERE_EMBED_MODEL: str 

    # Auth Configuration
    JWT_SECRET_KEY: str 
    JWT_ALGORITHM: str = "HS256"

    # Semantic Caching Configuration
    SEMANTIC_CACHE_THRESHOLD: float = 0.90
    SEMANTIC_CACHE_TTL: int = 86400  # Default 24 hours
    COHERE_EMBED_DIMENSION: int = 1024  # embed-english-v3.0 output dimension (used by HNSW index schema)

    # LLM API Keys
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY:Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    SAMBANOVA_API_KEY: Optional[str] = None
    NVIDIA_API_KEY: Optional[str] = None

settings = Settings()