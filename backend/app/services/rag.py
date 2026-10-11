from typing import List, Dict, Any, Optional
import math
import re
from sqlalchemy.orm import Session
from app.models.models import RetrievalDocument, Dataset
from app.core.config import settings

def simple_tokenize(text: str) -> List[str]:
    return [t.lower() for t in re.findall(r'\b[a-zA-Z0-9_]+\b', text)]

def compute_bm25_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
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
    Retrieval-Augmented Generation service providing schema, column definitions,
    and verified SQL examples scoped to authorized datasets.
    """
    
    @classmethod
    def retrieve_context(
        cls,
        db: Session,
        query: str,
        dataset_ids: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
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
            
            # Boost table schemas if query contains table words
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
        """Direct lookup for column meaning or description in documentation."""
        clean_col = column_name.strip().strip("?'\"` ").lower()
        query = db.query(RetrievalDocument).filter(
            RetrievalDocument.document_type.in_(["business_definition", "column_description"])
        )
        if dataset_ids:
            query = query.filter(RetrievalDocument.dataset_id.in_(dataset_ids))
            
        docs = query.all()
        # Check business definitions first
        for doc in docs:
            if doc.document_type == "business_definition":
                meta = doc.metadata_json or {}
                if meta.get("column_name", "").lower() == clean_col:
                    return doc.content
                    
        # Fall back to column descriptions
        for doc in docs:
            if doc.document_type == "column_description":
                meta = doc.metadata_json or {}
                if meta.get("column_name", "").lower() == clean_col:
                    return doc.content
        return None
