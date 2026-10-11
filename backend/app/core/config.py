import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "InsightSQL: Natural Language Data Explorer"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+psycopg://postgres:root@localhost:5432/ai_analyst"
    READONLY_DATABASE_URL: Optional[str] = None
    
    # Model configuration
    LLM_API_KEY: Optional[str] = os.getenv("LLM_API_KEY", None) or os.getenv("GROQ_API_KEY", None)
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY", None)
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    
    # Safety and limits
    MAX_UPLOAD_MB: int = 100
    MAX_IMPORT_ROWS: int = 1000000
    QUERY_TIMEOUT_MS: int = 5000
    MAX_RESULT_ROWS: int = 500
    
    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
