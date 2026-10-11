import pytest
from app.services.sql_policy import SQLPolicyValidator, SQLPolicyError

def test_valid_select_query():
    sql = 'SELECT city, SUM(sales) FROM "ds_tbl_orders" GROUP BY city'
    allowed = {"ds_tbl_orders"}
    formatted = SQLPolicyValidator.validate_and_format(sql, allowed, max_rows=100)
    assert "LIMIT 100" in formatted
    assert "ds_tbl_orders" in formatted

def test_reject_destructive_drop():
    sql = 'DROP TABLE ds_tbl_orders'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code in ["DISALLOWED_KEYWORD", "NOT_SELECT_STATEMENT"]

def test_reject_destructive_delete():
    sql = 'DELETE FROM ds_tbl_orders WHERE id = 1'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code in ["DISALLOWED_KEYWORD", "NOT_SELECT_STATEMENT"]

def test_reject_multiple_statements():
    sql = 'SELECT * FROM ds_tbl_orders; DROP TABLE ds_tbl_orders;'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code in ["MULTIPLE_STATEMENTS", "DISALLOWED_KEYWORD"]

def test_reject_unauthorized_table():
    sql = 'SELECT * FROM "secret_users_table"'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code == "UNAUTHORIZED_TABLE"

def test_reject_system_catalog_access():
    sql = 'SELECT * FROM pg_catalog.pg_tables'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code in ["UNAUTHORIZED_SCHEMA", "UNAUTHORIZED_TABLE"]

def test_reject_forbidden_function():
    sql = 'SELECT pg_sleep(10) FROM ds_tbl_orders'
    allowed = {"ds_tbl_orders"}
    with pytest.raises(SQLPolicyError) as exc:
        SQLPolicyValidator.validate_and_format(sql, allowed)
    assert exc.value.code == "DISALLOWED_FUNCTION"
