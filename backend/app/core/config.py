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
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: str = "openai/gpt-oss-20b"
    LLM_PROVIDER: str = "groq"
    
    # Safety and limits
    MAX_UPLOAD_MB: int = 100
    MAX_IMPORT_ROWS: int = 1000000
    QUERY_TIMEOUT_MS: int = 5000
    MAX_RESULT_ROWS: int = 500
    
    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")

settings = Settings()
