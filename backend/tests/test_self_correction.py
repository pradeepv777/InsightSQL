import json
import uuid
from types import SimpleNamespace

import pytest
from app.core.database import SessionLocal
from app.core.config import settings
from app.services.ingestion import IngestionService
from app.services.orchestrator import QueryOrchestrator
from app.services.sql_generator import SQLGenerator


@pytest.fixture
def dataset_id():
    """Ingest a small orders dataset and return (dataset_id, table_name)."""
    csv_data = """order_id,customer_id,city,sales,order_date
1001,Alice,Hyderabad,250.50,2026-01-15
1002,Bob,Bangalore,120.00,2026-01-16
1003,Charlie,Mumbai,450.75,2026-01-17
"""
    db = SessionLocal()
    try:
        dataset = IngestionService.ingest_dataset(
            db=db,
            filename="self_correction_orders.csv",
            content=csv_data.encode("utf-8")
        )
        table_name = dataset.tables[0].table_name
        return str(dataset.id), table_name
    finally:
        db.close()


def test_execution_failure_triggers_llm_self_correction(dataset_id, monkeypatch):
    """
    End-to-end self-correction: first query fails at execution (column referenced
    through a CTE that does not project it), the orchestrator re-prompts the repair
    LLM, revalidates the repaired SQL against the security policy, and executes it.
    All LLM calls are mocked so the test is deterministic and offline.
    """
    ds_id, table_name = dataset_id

    # Mimics the real-world failure: joined CTE projects only `city`,
    # final SELECT references `c.sales` which does not exist in the CTE.
    bad_sql = (
        f"WITH city_groups AS (SELECT city FROM {table_name} GROUP BY city)\n"
        f"SELECT c.city, c.sales FROM {table_name} m "
        f"JOIN city_groups c ON m.city = c.city "
        f"GROUP BY c.city, c.sales;"
    )
    fixed_sql = (
        f"SELECT city, SUM(sales) AS total_sales FROM {table_name} "
        f"GROUP BY city ORDER BY total_sales DESC;"
    )

    repair_calls = []

    def fake_generate_sql(cls, **kwargs):
        return {
            "status": "query",
            "sql": bad_sql,
            "explanation": "Aggregate comparison across cities.",
            "diagnostics": {"mode": "llm", "fresh_response": True}
        }

    def fake_regenerate(cls, failed_sql=None, db_error=None, **kwargs):
        repair_calls.append({"failed_sql": failed_sql, "db_error": db_error})
        return {
            "status": "repaired",
            "sql": fixed_sql,
            "explanation": "Root cause: c.sales not projected by CTE city_groups.",
            "diagnostics": {"mode": "execution_repair", "llm_model": "fake-model"}
        }

    monkeypatch.setattr(SQLGenerator, "generate_sql", classmethod(fake_generate_sql))
    monkeypatch.setattr(SQLGenerator, "regenerate_with_execution_error", classmethod(fake_regenerate))

    db = SessionLocal()
    try:
        res = QueryOrchestrator.process_message(
            db=db,
            conversation_id=str(uuid.uuid4()),
            question="Compare sales across cities",
            dataset_ids=[ds_id]
        )
    finally:
        db.close()

    assert res["status"] == "success"
    assert len(repair_calls) == 1
    assert "c.sales" in repair_calls[0]["failed_sql"]
    assert "does not exist" in repair_calls[0]["db_error"]
    # Final executed SQL must be the repaired one (the security policy may append its row cap)
    assert res["sql"].startswith(f"SELECT city, SUM(sales) AS total_sales FROM {table_name} GROUP BY city")
    assert res["row_count"] == 3


def test_self_correction_gives_up_when_repair_executes_bad_sql(dataset_id, monkeypatch):
    """
    Negative path: even the repaired query fails execution. The orchestrator must
    stop after one bounded retry, surface the original error, and record a failed
    QueryRun tagged EXECUTION_ERROR_AFTER_REPAIR.
    """
    from app.models.models import QueryRun

    ds_id, table_name = dataset_id
    bad_sql_1 = (
        f"WITH city_groups AS (SELECT city FROM {table_name} GROUP BY city)\n"
        f"SELECT c.city, c.sales FROM {table_name} m "
        f"JOIN city_groups c ON m.city = c.city "
        f"GROUP BY c.city, c.sales;"
    )
    bad_sql_2 = f"SELECT no_such_column FROM {table_name};"

    repair_calls = []

    def fake_generate_sql(cls, **kwargs):
        return {
            "status": "query",
            "sql": bad_sql_1,
            "explanation": "Aggregate comparison across cities.",
            "diagnostics": {"mode": "llm", "fresh_response": True}
        }

    def fake_regenerate(cls, failed_sql=None, db_error=None, **kwargs):
        repair_calls.append({"failed_sql": failed_sql, "db_error": db_error})
        return {
            "status": "repaired",
            "sql": bad_sql_2,
            "explanation": "Still referencing a non-projected column.",
            "diagnostics": {"mode": "execution_repair", "llm_model": "fake-model"}
        }

    monkeypatch.setattr(SQLGenerator, "generate_sql", classmethod(fake_generate_sql))
    monkeypatch.setattr(SQLGenerator, "regenerate_with_execution_error", classmethod(fake_regenerate))

    db = SessionLocal()
    conv_id = str(uuid.uuid4())
    try:
        res = QueryOrchestrator.process_message(
            db=db,
            conversation_id=conv_id,
            question="Compare sales across cities",
            dataset_ids=[ds_id]
        )
        qruns = db.query(QueryRun).filter(QueryRun.conversation_id == conv_id).all()
    finally:
        db.close()

    assert res["status"] == "failed"
    assert len(repair_calls) == 1
    assert len(res["warnings"]) >= 1
    assert len(qruns) == 1
    assert qruns[0].status == "failed"
    assert qruns[0].error_category == "EXECUTION_ERROR_AFTER_REPAIR"


def test_repair_generator_parses_llm_json(monkeypatch):
    """Unit: repair generator parses LLM JSON and returns a repaired plan."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key", raising=True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=True)
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o-mini", raising=True)

    payload = json.dumps({
        "status": "query",
        "sql": "SELECT 1 AS ok;",
        "explanation": "Root cause: missing projection in CTE."
    })
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
            )
        )
    )

    import openai as openai_module
    monkeypatch.setattr(openai_module, "OpenAI", lambda **kwargs: fake_client, raising=True)

    res = SQLGenerator.regenerate_with_execution_error(
        question="Compare sales across cities",
        table_name="t",
        columns=[{"name": "city", "type": "text"}],
        failed_sql="SELECT c.city, c.sales FROM t m JOIN cte c ON m.city = c.city;",
        db_error="column c.sales does not exist"
    )
    assert res["status"] == "repaired"
    assert res["sql"] == "SELECT 1 AS ok;"
    assert res["diagnostics"]["mode"] == "execution_repair"
    assert res["diagnostics"]["llm_model"] == "gpt-4o-mini"


def test_repair_generator_unfixable_response(monkeypatch):
    """Unit: when the LLM says the query is unfixable, no SQL is returned."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key", raising=True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=True)
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o-mini", raising=True)

    payload = json.dumps({
        "status": "unfixable",
        "sql": None,
        "explanation": "Required column is absent from the schema."
    })
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
            )
        )
    )

    import openai as openai_module
    monkeypatch.setattr(openai_module, "OpenAI", lambda **kwargs: fake_client, raising=True)

    res = SQLGenerator.regenerate_with_execution_error(
        question="q",
        table_name="t",
        columns=[],
        failed_sql="SELECT bad;",
        db_error="column bad does not exist"
    )
    assert res["status"] == "unfixable"
    assert res["sql"] is None


def test_repair_generator_offline_no_llm(monkeypatch):
    """Unit: without an API key the repair path degrades gracefully."""
    monkeypatch.setattr(settings, "LLM_API_KEY", None, raising=True)
    res = SQLGenerator.regenerate_with_execution_error(
        question="q",
        table_name="t",
        columns=[],
        failed_sql="SELECT 1;",
        db_error="error"
    )
    assert res["status"] == "no_llm"
    assert res["sql"] is None
