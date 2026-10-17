from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.models import Conversation, Message
from app.schemas.chat import MessageCreateRequest, MessageResponse, ConversationResponse
from app.services.orchestrator import QueryOrchestrator

router = APIRouter()

@router.post("", response_model=ConversationResponse)
def create_conversation(db: Session = Depends(get_db)):
    """Creates a new conversation session."""
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        created_at=str(conv.created_at)
    )

@router.get("", response_model=List[ConversationResponse])
def list_conversations(db: Session = Depends(get_db)):
    """Lists conversations."""
    convs = db.query(Conversation).order_by(Conversation.updated_at.desc()).all()
    return [
        ConversationResponse(
            id=c.id,
            title=c.title,
            created_at=str(c.created_at)
        )
        for c in convs
    ]

@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str, db: Session = Depends(get_db)):
    """Retrieves message history for a conversation."""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "metadata": m.metadata_json,
            "created_at": str(m.created_at)
        }
        for m in msgs
    ]

@router.post("/{conversation_id}/messages", response_model=MessageResponse)
def post_message(
    conversation_id: str,
    payload: MessageCreateRequest,
    db: Session = Depends(get_db)
):
    """Processes an analytical query or question within a conversation."""
    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
        
    try:
        result = QueryOrchestrator.process_message(
            db=db,
            conversation_id=conversation_id,
            question=payload.message,
            dataset_ids=payload.dataset_ids
        )
        return MessageResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        err_msg = str(exc)
        # Redact any credentials or secrets
        for term in ["key", "secret", "password", "token", "gsk_", "bearer "]:
            if term in err_msg.lower():
                err_msg = "An internal database or provider error occurred."
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QueryProcessingError",
                "message": f"Query processing failed: {err_msg}",
                "detail": err_msg
            }
        )

@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    """Deletes a conversation session and all its associated messages and query runs."""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    db.delete(conv)
    db.commit()
    return {"message": "Conversation deleted successfully"}
