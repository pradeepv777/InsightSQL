import pytest
import uuid
import os
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.services.orchestrator import QueryOrchestrator
from app.models.models import Dataset, DatasetTable, Conversation
from app.services.ingestion import IngestionService
from app.services.sql_policy import SQLPolicyValidator, SQLPolicyError
from app.main import app

client = TestClient(app)

@pytest.fixture(scope="module")
def exercise_dataset_id():
    db = SessionLocal()
    # Check if exercise/data dataset is already present
    ds = db.query(Dataset).filter(Dataset.display_name.ilike("%data%")).first()
    if ds and ds.status == "ready":
        ds_id = ds.id
        db.close()
        return ds_id

    # If not present, ingest a mock exercise CSV
    csv_content = (
        b"Duration,Date,Pulse,Maxpulse,Calories\n"
        b"60,'2020/12/01',110,130,409.1\n"
        b"60,'2020/12/02',117,145,479.0\n"
        b"60,'2020/12/03',103,135,340.0\n"
        b"45,'2020/12/04',109,175,282.4\n"
        b"30,'2020/12/09',109,133,195.1\n"
        b"45,'2020/12/10',100,120,\n"
    )
    dataset = IngestionService.ingest_dataset(
        db=db,
        filename="exercise_data.csv",
        content=csv_content,
        display_name="data"
    )
    ds_id = dataset.id
    db.close()
    return ds_id

def test_regression_1_highest_lowest_query_separate_tables(exercise_dataset_id):
    """
    Test 1: The original highest/lowest query returns separate named sections
    containing the complete rows with the highest and lowest non-null Calories values.
    """
    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    
    question = "separate tables containing the complete rows with the highest and lowest non-null Calories values"
    res = QueryOrchestrator.process_message(
        db=db,
        conversation_id=conv_id,
        question=question,
        dataset_ids=[exercise_dataset_id]
    )
    db.close()

    assert res["status"] == "success"
    assert res["sql"] is not None
    assert res["row_count"] == 2

    # Verify multiple named result sections are returned and separate
    assert res["sections"] is not None, "Expected separate named result sections"
    assert len(res["sections"]) == 2, f"Expected 2 sections (highest and lowest), got {len(res['sections'])}"
    
    # Check section titles and columns
    titles = [s["title"].lower() for s in res["sections"]]
    assert any("highest" in t for t in titles)
    assert any("least" in t or "lowest" in t for t in titles)

    # Verify rows in sections do not retain the heading column in their data columns
    for sec in res["sections"]:
        assert sec["row_count"] >= 1
        assert "heading" not in [c.lower() for c in sec["columns"]]
        assert any("calories" in c.lower() for c in sec["columns"])

def test_regression_2_follow_up_only_calories_column(exercise_dataset_id):
    """
    Test 2: The follow-up 'only calories column' preserves the previous highest/lowest
    filters, references the correct table from conversation history without explicit dataset_ids,
    and returns only the Calories column.
    """
    db = SessionLocal()
    conv_id = str(uuid.uuid4())

    # Turn 1: highest and lowest query
    q1 = "separate tables containing the complete rows with the highest and lowest non-null Calories values"
    r1 = QueryOrchestrator.process_message(
        db=db,
        conversation_id=conv_id,
        question=q1,
        dataset_ids=[exercise_dataset_id]
    )
    assert r1["status"] == "success"

    # Turn 2: Follow-up without explicit dataset_ids
    q2 = "only calories column"
    r2 = QueryOrchestrator.process_message(
        db=db,
        conversation_id=conv_id,
        question=q2,
        dataset_ids=None  # Conversation history should preserve active dataset
    )
    db.close()

    assert r2["status"] == "success", f"Follow-up query failed with error: {r2.get('warnings')}"
    assert r2["row_count"] == 2, f"Expected 2 rows preserved from highest/lowest, got {r2['row_count']}"
    assert r2["sql"] is not None

    # Verify only calories column (plus heading if in sections)
    if r2.get("sections"):
        for sec in r2["sections"]:
            assert len(sec["columns"]) == 1
            assert sec["columns"][0].lower() == "calories"
            # Values should be 479.0 and 195.1
            val = float(sec["rows"][0][0])
            assert val in [479.0, 195.1]
    else:
        # Non-heading data columns should be only calories
        data_cols = [c for c in r2["columns"] if c.lower() not in ["heading", "section"]]
        assert data_cols == ["calories"] or [c.lower() for c in data_cols] == ["calories"]

def test_regression_3_follow_up_filter_change_preserves_context(exercise_dataset_id):
    """
    Test 3: A follow-up that changes a filter preserves the previous request's context,
    selected table, and projections.
    """
    db = SessionLocal()
    conv_id = str(uuid.uuid4())

    # Turn 1: Query with initial filter
    q1 = "show all records where pulse > 80"
    r1 = QueryOrchestrator.process_message(
        db=db,
        conversation_id=conv_id,
        question=q1,
        dataset_ids=[exercise_dataset_id]
    )
    assert r1["status"] == "success"

    # Turn 2: Change filter while preserving previous context
    q2 = "filter to pulse > 100"
    r2 = QueryOrchestrator.process_message(
        db=db,
        conversation_id=conv_id,
        question=q2,
        dataset_ids=None
    )
    db.close()

    assert r2["status"] == "success"
    assert r2["sql"] is not None
    assert "pulse" in r2["sql"].lower()
    assert "> 100" in r2["sql"] or ">100" in r2["sql"]
    assert r2["row_count"] > 0

def test_regression_4_sql_validation_failures_and_sanitized_errors(exercise_dataset_id):
    """
    Test 4: SQL validation failures (unauthorized tables, dangerous statements)
    and provider errors are cleanly handled and never expose secrets or API keys.
    """
    # 4a: Test unauthorized table access rejection
    with pytest.raises(SQLPolicyError) as exc_info:
        SQLPolicyValidator.validate_and_format(
            "SELECT * FROM unauthorized_secret_table",
            allowed_tables={"ds_tbl_allowed"}
        )
    assert "not in the authorized datasets" in exc_info.value.message

    # 4b: Test disallowed mutation keywords
    with pytest.raises(SQLPolicyError) as exc_info:
        SQLPolicyValidator.validate_and_format(
            "DROP TABLE ds_tbl_allowed",
            allowed_tables={"ds_tbl_allowed"}
        )
    assert "Prohibited SQL keyword" in exc_info.value.message

    # 4c: Test API error endpoint sanitization
    # Post empty or invalid request to verify HTTP status code and sanitized response
    conv_res = client.post("/api/v1/conversations")
    assert conv_res.status_code == 200
    conv_id = conv_res.json()["id"]

    # Post an empty message to test 400 validation
    bad_msg_res = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"message": "   "}
    )
    assert bad_msg_res.status_code == 400

    # Ensure no API keys or secrets are leaked in error responses
    raw_text = bad_msg_res.text
    for term in ["gsk_", "sk-", "password", "secret_key"]:
        assert term not in raw_text.lower()
