from typing import Set
import re
import sqlglot
from sqlglot import exp
from app.core.config import settings

# Functions prohibited because they interact with server filesystem, process execution, or sleep
DISALLOWED_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_write_file",
    "pg_write_binary_file", "pg_ls_dir", "pg_stat_file", "query_to_xml",
    "dblink", "dblink_exec", "current_setting", "set_config"
}

# PostgreSQL internal schemas prohibited to prevent metadata exfiltration or database enumeration
DISALLOWED_SCHEMAS = {
    "pg_catalog", "information_schema", "pg_toast"
}

# High-risk mutation and administrative keywords checked as an initial regex barrier
DISALLOWED_KEYWORDS = [
    "drop ", "delete ", "insert ", "update ", "truncate ", "alter ",
    "create ", "grant ", "revoke ", "copy ", "vacuum ", "reindex ",
    "execute ", "call ", "into outfile", "load_file"
]

class SQLPolicyError(Exception):
    """Raised when a candidate query violates AST security guardrails."""
    def __init__(self, message: str, code: str = "SQL_REJECTED"):
        super().__init__(message)
        self.message = message
        self.code = code

class SQLPolicyValidator:
    """
    PostgreSQL-Aware AST Security Validator & Sanitization Engine.
    
    Why AST (Abstract Syntax Tree) is required instead of Regex:
      - Regex is easily bypassed by SQL obfuscation, comments (e.g., `/*comment*/DROP`),
        or strings that mimic keywords.
      - AST parsing converts code into a semantic tree of statements and expressions,
        ensuring guaranteed structural enforcement (read-only single SELECT, strict table allowlist).
    """
    
    @classmethod
    def validate_and_format(
        cls,
        sql: str,
        allowed_tables: Set[str],
        max_rows: int = settings.MAX_RESULT_ROWS
    ) -> str:
        """
        Validates that a query conforms to strict read-only analytical rules:
          Step 1: Rejects obvious mutation keywords via regex.
          Step 2: Parses SQL into an AST using sqlglot PostgreSQL dialect.
          Step 3: Blocks semicolon-chained multiple statements.
          Step 4: Ensures root expression is strictly a SELECT or UNION.
          Step 5: Traverses AST to block forbidden functions, system schemas, and unauthorized tables.
          Step 6: Clamps row limits to MAX_RESULT_ROWS to prevent memory exhaustion.
        """
        if not sql or not sql.strip():
            raise SQLPolicyError("Empty SQL query proposed", "EMPTY_QUERY")
            
        clean_sql = sql.strip().rstrip(";")
        
        # 1. Regex check for prohibited mutation keywords (fast-reject barrier)
        lower_sql = clean_sql.lower()
        for kw in DISALLOWED_KEYWORDS:
            if re.search(r'\b' + re.escape(kw.strip()) + r'\b', lower_sql):
                raise SQLPolicyError(f"Prohibited SQL keyword detected: {kw.strip().upper()}", "DISALLOWED_KEYWORD")

        # 2. Parse using sqlglot with PostgreSQL dialect rules
        try:
            expressions = sqlglot.parse(clean_sql, read="postgres")
        except Exception as e:
            raise SQLPolicyError(f"SQL syntax error: {str(e)}", "SYNTAX_ERROR")

        if not expressions:
            raise SQLPolicyError("No executable SQL statements found", "EMPTY_QUERY")
            
        # Defense against SQL injection via semicolon chaining (e.g. `SELECT 1; DROP TABLE users;`)
        if len(expressions) > 1:
            raise SQLPolicyError("Multiple statements are strictly forbidden", "MULTIPLE_STATEMENTS")

        root = expressions[0]
        
        # 3. Ensure root expression is strictly a SELECT or UNION / CTE wrapping SELECT
        if not isinstance(root, (exp.Select, exp.Union)):
            raise SQLPolicyError("Only SELECT queries are permitted", "NOT_SELECT_STATEMENT")

        # 4. AST traversal for forbidden functions, table access, and schemas
        extracted_tables: Set[str] = set()
        
        for node in root.walk():
            # Check for disallowed functions (e.g. pg_sleep denial of service or pg_read_file exfiltration)
            if isinstance(node, exp.Anonymous):
                func_name = node.name.lower()
                if func_name in DISALLOWED_FUNCTIONS:
                    raise SQLPolicyError(f"Prohibited function execution: {func_name}", "DISALLOWED_FUNCTION")
            
            # Check Table references and schema qualifications
            if isinstance(node, exp.Table):
                table_name = node.name.lower()
                schema_name = node.db.lower() if node.db else ""
                
                # Block queries against internal PostgreSQL schemas
                if schema_name in DISALLOWED_SCHEMAS:
                    raise SQLPolicyError(f"Access to system schema '{schema_name}' is forbidden", "UNAUTHORIZED_SCHEMA")
                
                # Ignore internal Common Table Expression (CTE) aliases (e.g. `WITH temp_tbl AS (...)`)
                ctes = [cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)]
                if table_name not in ctes:
                    extracted_tables.add(table_name)

        # 5. Check authorized tables against tenant/dataset allowlist
        normalized_allowed = {t.lower() for t in allowed_tables}
        for tbl in extracted_tables:
            if tbl not in normalized_allowed:
                raise SQLPolicyError(
                    f"Query attempts to access table '{tbl}' which is not in the authorized datasets",
                    "UNAUTHORIZED_TABLE"
                )

        # 6. Apply row limit (clamps unbounded `SELECT *` to MAX_RESULT_ROWS to prevent out-of-memory crashes)
        limit_exp = root.args.get("limit")
        if limit_exp is None:
            root = root.limit(max_rows)
        else:
            try:
                curr_limit = int(str(limit_exp.expression))
                if curr_limit > max_rows:
                    root = root.limit(max_rows)
            except Exception:
                pass

        return root.sql(dialect="postgres")
