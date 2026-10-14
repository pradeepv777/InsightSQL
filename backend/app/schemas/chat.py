from typing import List, Optional, Any, Dict
from pydantic import BaseModel

class MessageCreateRequest(BaseModel):
    message: str
    dataset_ids: Optional[List[str]] = None
    include_sql: bool = True
    visualization: str = "auto"

class VisualizationConfig(BaseModel):
    type: str
    x: str
    y: str

class ResultSection(BaseModel):
    title: str
    columns: List[str]
    rows: List[List[Any]]
    row_count: int

class MessageResponse(BaseModel):
    message_id: str
    answer: str
    sql: Optional[str] = None
    columns: List[str] = []
    rows: List[List[Any]] = []
    row_count: int = 0
    sections: Optional[List[ResultSection]] = None
    visualization: Optional[VisualizationConfig] = None
    status: str
    clarification_question: Optional[str] = None
    warnings: List[str] = []

class DatasetResponse(BaseModel):
    id: str
    display_name: str
    name: Optional[str] = None
    source_filename: str
    source_format: str
    status: str
    row_count: int
    created_at: Optional[str] = None
    error_summary: Optional[str] = None

class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: Optional[str] = None
