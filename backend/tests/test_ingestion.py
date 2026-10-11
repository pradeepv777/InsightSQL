import pytest
from io import BytesIO
import pandas as pd
from app.core.database import SessionLocal, engine
from app.models.models import Dataset, DatasetTable, ColumnMetadata
from app.services.ingestion import IngestionService, sanitize_identifier

def test_sanitize_identifier():
    assert sanitize_identifier("Order ID") == "order_id"
    assert sanitize_identifier("Total Sales ($)") == "total_sales"
    assert sanitize_identifier("123_number") == "col_123_number"
    assert sanitize_identifier("Customer Name   ") == "customer_name"

def test_ingest_csv_roundtrip():
    csv_data = """order_id,customer_name,city,sales,order_date
1001,Alice,Hyderabad,250.50,2026-01-15
1002,Bob,Bangalore,120.00,2026-01-16
1003,Charlie,Hyderabad,450.75,2026-01-17
1004,David,Mumbai,90.20,2026-01-18
1005,Alice,Hyderabad,310.00,2026-01-19
"""
    db = SessionLocal()
    try:
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename="orders.csv",
            content=csv_data.encode("utf-8")
        )
        assert dataset.status == "ready"
        assert dataset.row_count == 5
        assert len(dataset.tables) == 1
        
        table = dataset.tables[0]
        assert table.row_count == 5
        assert len(table.columns) == 5
        
        col_names = [c.column_name for c in table.columns]
        assert "city" in col_names
        assert "sales" in col_names
    finally:
        db.close()

def test_ingest_json():
    json_data = b'[{"id": 1, "product": "Laptop", "price": 999.99}, {"id": 2, "product": "Mouse", "price": 25.50}]'
    db = SessionLocal()
    try:
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename="products.json",
            content=json_data
        )
        assert dataset.status == "ready"
        assert dataset.row_count == 2
    finally:
        db.close()

def test_ingest_parquet():
    df = pd.DataFrame({
        "user_id": [1, 2, 3],
        "score": [88.5, 92.0, 79.5]
    })
    bio = BytesIO()
    df.to_parquet(bio)
    db = SessionLocal()
    try:
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename="scores.parquet",
            content=bio.getvalue()
        )
        assert dataset.status == "ready"
        assert dataset.row_count == 3
    finally:
        db.close()
