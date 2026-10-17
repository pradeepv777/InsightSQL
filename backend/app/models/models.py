import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base

def gen_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Dataset(Base):
    __tablename__ = "datasets"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id = Column(String(100), default="default_user", nullable=False)
    display_name = Column(String(255), nullable=False)
    source_filename = Column(String(255), nullable=False)
    source_format = Column(String(50), nullable=False)
    status = Column(String(50), default="processing", nullable=False) # processing, ready, failed
    row_count = Column(Integer, default=0)
    error_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    completed_at = Column(DateTime, nullable=True)
    
    tables = relationship("DatasetTable", back_populates="dataset", cascade="all, delete-orphan")
    documents = relationship("RetrievalDocument", back_populates="dataset", cascade="all, delete-orphan")

class DatasetTable(Base):
    __tablename__ = "dataset_tables"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    schema_name = Column(String(100), default="public", nullable=False)
    table_name = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(255), nullable=False)
    row_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)
    
    dataset = relationship("Dataset", back_populates="tables")
    columns = relationship("ColumnMetadata", back_populates="table", cascade="all, delete-orphan")

class ColumnMetadata(Base):
    __tablename__ = "column_metadata"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    dataset_table_id = Column(String(36), ForeignKey("dataset_tables.id", ondelete="CASCADE"), nullable=False)
    column_name = Column(String(100), nullable=False)
    database_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    nullable = Column(Boolean, default=True)
    profile_json = Column(JSON, nullable=True)
    
    table = relationship("DatasetTable", back_populates="columns")

class RetrievalDocument(Base):
    __tablename__ = "retrieval_documents"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True)
    document_type = Column(String(50), nullable=False) # table_schema, column_description, business_definition, sql_example
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    
    dataset = relationship("Dataset", back_populates="documents")

class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id = Column(String(100), default="default_user", nullable=False)
    title = Column(String(255), default="New Analysis")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    query_runs = relationship("QueryRun", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False) # user, assistant, system
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    
    conversation = relationship("Conversation", back_populates="messages")

class QueryRun(Base):
    __tablename__ = "query_runs"
    
    id = Column(String(36), primary_key=True, default=gen_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    generated_sql = Column(Text, nullable=True)
    status = Column(String(50), nullable=False) # success, rejected, failed, timeout
    execution_ms = Column(Integer, nullable=True)
    error_category = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now)
    
    conversation = relationship("Conversation", back_populates="query_runs")
