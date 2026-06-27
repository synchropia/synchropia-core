from typing import Optional
from pydantic import PostgresDsn, Field 
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # GitHub App Credentials
    GITHUB_APP_ID: str
    GITHUB_APP_PRIVATE_KEY_PATH: str
    GITHUB_INSTALLATION_ID: str
    
    # Mem0 credentials 
    MEM0_API_KEY:str

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
    
    # Auth Configuration
    JWT_SECRET_KEY: str 
    JWT_ALGORITHM: str = "HS256"

    # Semantic Caching Configuration
    SEMANTIC_CACHE_THRESHOLD: float = 0.85
    SEMANTIC_CACHE_TTL: int = 86400  # Default 24 hours

    # LLM API Keys
    GROQ_API_KEY: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    SAMBANOVA_API_KEY: Optional[str] = None
    NVIDIA_API_KEY: Optional[str] = None

settings = Settings()