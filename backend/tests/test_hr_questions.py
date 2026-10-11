import pytest
import uuid
from app.core.database import SessionLocal
from app.services.orchestrator import QueryOrchestrator
from app.models.models import Dataset, DatasetTable
from app.services.ingestion import IngestionService
import os

@pytest.fixture(scope="module")
def hr_dataset_id():
    db = SessionLocal()
    # Check if HR dataset is already ingested
    ds = db.query(Dataset).filter(Dataset.display_name.ilike("%HR%Employee%Attrition%")).first()
    if ds and ds.status == "ready":
        ds_id = ds.id
        db.close()
        return ds_id

    # If not, check if CSV file exists in workspace
    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "WA_Fn-UseC_-HR-Employee-Attrition.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "rb") as f:
            content = f.read()
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename="WA_Fn-UseC_-HR-Employee-Attrition.csv",
            content=content,
            display_name="WA_Fn-UseC_-HR-Employee-Attrition"
        )
        ds_id = dataset.id
        db.close()
        return ds_id
        
    db.close()
    pytest.skip("HR Employee Attrition dataset not found in database or workspace")

def test_question_1_total_employees(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "How many employees are in this dataset?", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 1
    assert res["rows"][0][0] == "1470" or res["rows"][0][0] == 1470
    assert "1,470" in res["answer"]

def test_question_2_employees_left(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "How many employees left the company?", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 1
    assert res["rows"][0][0] == "237" or res["rows"][0][0] == 237
    assert "237" in res["answer"]

def test_question_3_avg_monthly_income_left(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "What is the average monthly income of employees who left?", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 1
    # Approximately 4787.09
    val = float(res["rows"][0][0])
    assert abs(val - 4787.09) < 0.1
    assert "4,787.09" in res["answer"]

def test_question_4_attrition_by_department(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "Compare attrition rates across departments.", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 3
    # Accept any column name containing 'attrition_rate' (LLM may use _pct or _percent)
    rate_col = next((c for c in res["columns"] if "attrition_rate" in c), None)
    assert rate_col is not None, f"Expected an attrition_rate column, got: {res['columns']}"
    # Check department rates
    # Sales: 20.63%, Human Resources: 19.05%, Research & Development: 13.84%
    assert any("Sales" in str(row[0]) for row in res["rows"])
    assert any("Human Resources" in str(row[0]) for row in res["rows"])
    assert any("Research & Development" in str(row[0]) for row in res["rows"])
    assert res["visualization"] is not None
    assert res["visualization"]["type"] == "bar"

def test_question_5_attrition_by_overtime(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    res = QueryOrchestrator.process_message(
        db, conv_id, "Compare attrition rates between employees who work overtime and those who do not.", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 2
    # Accept any column name containing 'attrition_rate' (LLM may use _pct or _percent)
    rate_col = next((c for c in res["columns"] if "attrition_rate" in c), None)
    assert rate_col is not None, f"Expected an attrition_rate column, got: {res['columns']}"
    # Overtime Yes: ~30.53%, Overtime No: ~10.44%
    yes_row = next(r for r in res["rows"] if r[0] == "Yes")
    no_row = next(r for r in res["rows"] if r[0] == "No")
    rate_idx = res["columns"].index(rate_col)
    assert float(yes_row[rate_idx]) > float(no_row[rate_idx])
    assert res["visualization"] is not None
    assert res["visualization"]["type"] == "bar"

def test_question_6_follow_up_overtime_income(hr_dataset_id):
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    # Turn 1: Overtime comparison
    QueryOrchestrator.process_message(
        db, conv_id, "Compare attrition rates between employees who work overtime and those who do not.", [hr_dataset_id]
    )
    # Turn 2: Follow-up question
    res = QueryOrchestrator.process_message(
        db, conv_id, "Follow up on the previous comparison and request the average monthly income for the group with higher attrition.", [hr_dataset_id]
    )
    db.close()
    assert res["status"] == "success"
    assert res["row_count"] == 1
    val = float(res["rows"][0][0])
    # The group with higher attrition is Overtime='Yes' (avg income ~ 6548.55)
    assert abs(val - 6548.55) < 1.0
    assert "6,548.55" in res["answer"]
