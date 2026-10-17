from typing import List, Dict, Any, Optional
import re
from sqlalchemy.orm import Session
from app.models.models import RetrievalDocument
from app.core.config import settings

def simple_tokenize(text: str) -> List[str]:
    """Extracts lowercase alphanumeric words and column tokens."""
    return [t.lower() for t in re.findall(r'\b[a-zA-Z0-9_]+\b', text)]

def compute_bm25_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
    """
    Computes BM25 term-frequency relevance score with length normalization.
    Parameters:
      k1 = 1.5 (term frequency saturation: prevents repeated words from dominating score)
      b  = 0.75 (length normalization against nominal avg doc length 50)
    
    Why lexical matching beats dense vector embeddings for Text-to-SQL schema retrieval:
      - Column names (e.g. 'emp_id', 'monthly_income', 'dept_no') require exact lexical matching.
      - Dense vector embeddings often suffer from semantic drift (e.g. confusing 'gross_revenue' with 'net_income').
      - In-memory lexical matching runs sub-millisecond with zero external embedding latency.
    """
    score = 0.0
    doc_len = len(doc_tokens)
    if doc_len == 0:
        return 0.0
    for q in query_tokens:
        count = doc_tokens.count(q)
        if count > 0:
            score += (count / doc_len) * (1.5 + 1) / (count / doc_len + 1.5 * (1 - 0.75 + 0.75 * doc_len / 50.0))
    return score

class RAGService:
    """
    Retrieval-Augmented Generation (RAG) Service for Database Schema Grounding.
    Indexes and retrieves schema definitions, business glossary terms, and sample values
    scoped strictly to authorized active datasets to fit within LLM prompt limits.
    """
    
    @classmethod
    def retrieve_context(
        cls,
        db: Session,
        query: str,
        dataset_ids: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieves the top-K most relevant schema documentation blocks for the user's question.
        - Isolation: Filters documents strictly by authorized dataset_ids.
        - Prioritization: Adds a +0.2 boost for overall table schema summaries so the LLM
          always receives the primary table structure.
        """
        query_stmt = db.query(RetrievalDocument)
        if dataset_ids:
            query_stmt = query_stmt.filter(RetrievalDocument.dataset_id.in_(dataset_ids))
            
        docs = query_stmt.all()
        if not docs:
            return []
            
        query_tokens = simple_tokenize(query)
        scored_docs = []
        
        for doc in docs:
            doc_tokens = simple_tokenize(doc.content)
            score = compute_bm25_score(query_tokens, doc_tokens)
            
            # Boost table schemas so the overall table definition is always prioritized
            if doc.document_type == "table_schema":
                score += 0.2
            
            scored_docs.append((score, doc))
            
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        top_results = scored_docs[:top_k]
        
        return [
            {
                "id": doc.id,
                "dataset_id": doc.dataset_id,
                "type": doc.document_type,
                "content": doc.content,
                "metadata": doc.metadata_json,
                "score": round(score, 4)
            }
            for score, doc in top_results if score > 0.05 or doc.document_type == "table_schema"
        ]

    @classmethod
    def get_column_definition(
        cls,
        db: Session,
        column_name: str,
        dataset_ids: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Direct fast-path lookup for column meaning or business glossary definitions.
        Checks curated 'business_definition' documents first, falling back to 'column_description'.
        """
        clean_col = column_name.strip().strip("?'\"` ").lower()
        query = db.query(RetrievalDocument).filter(
            RetrievalDocument.document_type.in_(["business_definition", "column_description"])
        )
        if dataset_ids:
            query = query.filter(RetrievalDocument.dataset_id.in_(dataset_ids))
            
        docs = query.all()
        # Check curated business definitions first
        for doc in docs:
            if doc.document_type == "business_definition":
                meta = doc.metadata_json or {}
                if meta.get("column_name", "").lower() == clean_col:
                    return doc.content
                    
        # Fall back to automated column descriptions
        for doc in docs:
            if doc.document_type == "column_description":
                meta = doc.metadata_json or {}
                if meta.get("column_name", "").lower() == clean_col:
                    return doc.content
        return None
