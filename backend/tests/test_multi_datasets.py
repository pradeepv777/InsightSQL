import pytest
import uuid
from app.core.database import SessionLocal
from app.services.orchestrator import QueryOrchestrator
from app.services.ingestion import IngestionService

@pytest.fixture(scope="module")
def medical_dataset_id():
    db = SessionLocal()
    csv_content = (
        b"patient_id,age,bmi,charges,smoker\n"
        b"1,19,27.9,16884.92,yes\n"
        b"2,18,33.77,1725.55,no\n"
        b"3,28,33.0,4449.46,no\n"
        b"4,33,22.7,21984.47,yes\n"
        b"5,32,28.88,3866.86,no\n"
    )
    dataset = IngestionService.ingest_dataset(
        db=db,
        filename="test_medical.csv",
        content=csv_content,
        display_name="Medical Data"
    )
    ds_id = dataset.id
    db.close()
    return ds_id

@pytest.fixture(scope="module")
def housing_dataset_id():
    db = SessionLocal()
    csv_content = (
        b"property_id,city,bedrooms,price,status\n"
        b"101,Austin,3,450000.0,sold\n"
        b"102,Dallas,4,550000.0,active\n"
        b"103,Austin,2,320000.0,active\n"
        b"104,Houston,5,650000.0,sold\n"
        b"105,Dallas,3,410000.0,active\n"
    )
    dataset = IngestionService.ingest_dataset(
        db=db,
        filename="test_housing.csv",
        content=csv_content,
        display_name="Housing Data"
    )
    ds_id = dataset.id
    db.close()
    return ds_id

def test_medical_aggregation_and_count(medical_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    
    # 1. Row count query on healthcare schema
    res1 = QueryOrchestrator.process_message(
        db, conv_id, "How many records are in this dataset?", [medical_dataset_id]
    )
    assert res1["status"] == "success"
    assert res1["row_count"] == 1
    assert int(res1["rows"][0][0]) == 5
    
    # 2. Metric aggregation query
    res2 = QueryOrchestrator.process_message(
        db, conv_id, "What is the average charges?", [medical_dataset_id]
    )
    assert res2["status"] == "success"
    assert "AVG" in res2["sql"].upper()
    assert float(res2["rows"][0][0]) > 0
    db.close()

def test_medical_rate_breakdown(medical_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "Compare smoker rates across age", [medical_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] >= 1
    assert any("rate" in c.lower() or "pct" in c.lower() for c in res["columns"])

def test_housing_filter_and_follow_up(housing_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    
    # Turn 1: Filter query on housing schema
    res1 = QueryOrchestrator.process_message(
        db, conv_id, "Show properties where bedrooms > 3", [housing_dataset_id]
    )
    assert res1["status"] == "success"
    assert res1["row_count"] >= 1
    assert "bedrooms" in res1["sql"].lower()

    # Turn 2: Follow-up column refinement
    res2 = QueryOrchestrator.process_message(
        db, conv_id, "only price column", [housing_dataset_id]
    )
    db.close()
    assert res2["status"] == "success"
    assert "price" in res2["sql"].lower()
    data_cols = [c.lower() for c in res2["columns"] if c.lower() not in ["heading", "section"]]
    assert "price" in data_cols

def test_housing_highest_lowest_boundary(housing_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "Show highest and lowest price values in separate tables", [housing_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["sql"] is not None
    assert "MAX" in res["sql"].upper() or "MIN" in res["sql"].upper() or "LIMIT" in res["sql"].upper()
