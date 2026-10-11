from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db, engine
from app.models.models import Dataset, DatasetTable, ColumnMetadata
from app.services.ingestion import IngestionService
from app.schemas.chat import DatasetResponse

router = APIRouter()

@router.post("", response_model=DatasetResponse)
async def upload_dataset(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Uploads and ingests a structured data file (CSV, XLSX, Parquet, JSON)."""
    try:
        content = await file.read()
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename=file.filename,
            content=content
        )
        return DatasetResponse(
            id=dataset.id,
            display_name=dataset.display_name,
            source_filename=dataset.source_filename,
            source_format=dataset.source_format,
            status=dataset.status,
            row_count=dataset.row_count,
            created_at=str(dataset.created_at) if dataset.created_at else None,
            error_summary=dataset.error_summary
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=List[DatasetResponse])
def list_datasets(db: Session = Depends(get_db)):
    """Lists all uploaded datasets."""
    datasets = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
    return [
        DatasetResponse(
            id=d.id,
            display_name=d.display_name,
            source_filename=d.source_filename,
            source_format=d.source_format,
            status=d.status,
            row_count=d.row_count,
            created_at=str(d.created_at) if d.created_at else None,
            error_summary=d.error_summary
        )
        for d in datasets
    ]

@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    """Retrieves dataset metadata."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetResponse(
        id=dataset.id,
        display_name=dataset.display_name,
        source_filename=dataset.source_filename,
        source_format=dataset.source_format,
        status=dataset.status,
        row_count=dataset.row_count,
        created_at=str(dataset.created_at) if dataset.created_at else None,
        error_summary=dataset.error_summary
    )

@router.get("/{dataset_id}/preview")
def preview_dataset(dataset_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Returns sample rows and columns from the dataset's table."""
    ds_table = db.query(DatasetTable).filter(DatasetTable.dataset_id == dataset_id).first()
    if not ds_table:
        raise HTTPException(status_code=404, detail="Dataset table not found")
        
    with engine.connect() as conn:
        res = conn.execute(text(f'SELECT * FROM "{ds_table.table_name}" LIMIT :lim'), {"lim": min(limit, 100)})
        cols = list(res.keys())
        rows = [list(r) for r in res.fetchall()]
        
    return {
        "columns": cols,
        "rows": rows,
        "total_rows": ds_table.row_count
    }

@router.get("/{dataset_id}/profile")
def profile_dataset(dataset_id: str, db: Session = Depends(get_db)):
    """Returns column-level profile metrics and missing-value counts."""
    ds_table = db.query(DatasetTable).filter(DatasetTable.dataset_id == dataset_id).first()
    if not ds_table:
        raise HTTPException(status_code=404, detail="Dataset table not found")
        
    cols = db.query(ColumnMetadata).filter(ColumnMetadata.dataset_table_id == ds_table.id).all()
    return {
        "table_name": ds_table.table_name,
        "row_count": ds_table.row_count,
        "columns": [
            {
                "name": c.column_name,
                "type": c.database_type,
                "nullable": c.nullable,
                "profile": c.profile_json
            }
            for c in cols
        ]
    }

@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    """Deletes dataset and its PostgreSQL table."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
        
    for tbl in dataset.tables:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS "{tbl.table_name}"'))
        except Exception:
            pass
            
    db.delete(dataset)
    db.commit()
    return {"message": "Dataset deleted successfully"}
