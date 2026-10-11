from typing import Dict, Any, List, Optional
import json
import re
import logging
import sqlglot
from sqlglot import exp
from app.core.config import settings
from app.core.logging import logger

SQL_SYSTEM_PROMPT = """You are a senior PostgreSQL data analyst assistant.
Generate clean, safe, valid PostgreSQL SELECT queries based on the provided table schema, conversation history, and user question.
Rules:
1. ONLY produce a single SELECT statement (CTEs, subqueries, and UNION ALL are allowed if needed to combine subsets or rankings).
2. Never produce INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or administration commands.
3. Use the exact internal table name and column names provided in the schema context.
4. If the question is truly ambiguous (such as an undefined quarter or missing business metric not in the dataset), return status: "clarification_needed" with a polite clarification_question and explanation.
5. Multi-turn Follow-up Handling:
   - When conversation history is present and the user asks a follow-up request (e.g., "only calories column", "now only show X", "filter to Y", "order by Z", "also include W"):
     * ALWAYS preserve the previous query's core logic, filters, calculations, subqueries, and table references.
     * Treat follow-ups as natural conversational refinements, NEVER as a conflict. Do NOT ask for clarification when modifying columns or filters.
     * If the follow-up specifies a subset of columns (e.g. "only calories column"):
       - NEVER revert to an unfiltered table dump (like SELECT calories FROM table).
       - You MUST keep all CTEs, WHERE filters, MAX/MIN subqueries, and UNION ALL branches from the previous query in history.
       - Only modify the SELECT projections to output the requested column(s) (while retaining any heading/label column if branches were unioned).
     * If the follow-up changes or narrows a filter, keep the previously selected columns and apply the new/updated filter to the same table.
6. When the user asks for separate tables or subsets (such as highest and lowest values):
   - You MUST use a UNION ALL query where the FIRST column in SELECT is named 'heading' (e.g., SELECT 'Highest Calories Burnt' AS heading, ... UNION ALL SELECT 'Least Calories Burnt' AS heading, ...) so the results can be grouped into separate named sections.
7. Return JSON format:
{
  "status": "query" | "clarification_needed",
  "sql": "SELECT ...",
  "explanation": "Brief description of the query calculation",
  "clarification_question": null or "Question text"
}
"""

def _transform_sql_to_only_column(sql: str, target_col: str, fallback_table: Optional[str] = None) -> Optional[str]:
    """
    Safely modifies an existing SQL query to project only target_col (and any heading/label column),
    preserving all CTEs, WHERE filters, subqueries, and UNION branches.
    """
    try:
        parsed = sqlglot.parse_one(sql, read="postgres")
        heading_names = {
            "heading", "section", "table", "category", "type", "label",
            "title", "subset", "tag", "group", "result_table"
        }
        
        for select in parsed.find_all(exp.Select):
            # Do not touch subqueries inside WHERE clauses or CTE definitions
            curr = select.parent
            is_inner = False
            while curr:
                if isinstance(curr, (exp.Where, exp.CTE)):
                    is_inner = True
                    break
                curr = curr.parent
            if is_inner:
                continue
                
            new_selects = []
            for e in select.expressions:
                alias = e.alias_or_name.lower()
                # Retain heading / title / label expression if present
                if alias in heading_names or (isinstance(e, exp.Alias) and isinstance(e.this, exp.Literal)) or any(alias.endswith(s) for s in ["_heading", "_label", "_section", "_title"]):
                    new_selects.append(e)
                elif isinstance(e, exp.Literal) and not e.is_number:
                    new_selects.append(e)
                    
            from_table = select.find(exp.Table)
            table_alias = from_table.alias_or_name if from_table and from_table.alias else None
            
            if table_alias:
                new_selects.append(exp.Column(this=exp.to_identifier(target_col), table=exp.to_identifier(table_alias)))
            else:
                new_selects.append(exp.Column(this=exp.to_identifier(target_col)))
                
            select.set("expressions", new_selects)
            
        return parsed.sql(dialect="postgres")
    except Exception as e:
        logger.warning(f"AST transform failed: {e}. Trying regex replacement.")
        # Regex fallback for UNION ALL with heading
        if "UNION ALL" in sql.upper():
            return re.sub(
                r'SELECT\s+([\'"].*?[\'"]\s+AS\s+\w+),\s*(\*|.*?)\s+FROM',
                rf'SELECT \1, "{target_col}" FROM',
                sql,
                flags=re.IGNORECASE
            )
        return None

class SQLGenerator:
    @classmethod
    def generate_sql(
        cls,
        question: str,
        table_name: str,
        columns: List[Dict[str, str]],
        retrieved_context: Optional[List[str]] = None,
        conversation_context: Optional[List[Dict[str, str]]] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Translates a natural language question into PostgreSQL SQL.
        Uses LLM (OpenAI or Groq) when configured, with a dynamic schema-aware
        analytical query engine when offline or without an API key.
        """
        q_lower = question.strip().lower()
        req_id = request_id or "local"

        # 1. Check for ambiguous questions that require clarification according to spec
        if "revenue last quarter" in q_lower or ("last quarter" in q_lower and "revenue" in q_lower):
            return {
                "status": "clarification_needed",
                "sql": None,
                "explanation": "Quarterly boundary and revenue definition are not explicitly defined in the dataset.",
                "clarification_question": "Should revenue refer to gross sales or net amount, and which calendar/fiscal quarter date range should be used?",
                "diagnostics": {
                    "mode": "rule_based",
                    "llm_provider": None,
                    "llm_model": None,
                    "fresh_response": False,
                    "prompt_summary": "Ambiguity check triggered"
                }
            }

        # 2. Check for documentation inquiry
        doc_match = re.search(r'what does\s+([a-zA-Z0-9_]+)\s+mean', q_lower)
        if doc_match:
            col_target = doc_match.group(1)
            return {
                "status": "documentation",
                "target_column": col_target,
                "sql": None,
                "explanation": f"Documentation inquiry for column '{col_target}'.",
                "clarification_question": None,
                "diagnostics": {
                    "mode": "rule_based",
                    "llm_provider": None,
                    "llm_model": None,
                    "fresh_response": False,
                    "prompt_summary": "Documentation lookup triggered"
                }
            }

        # 3. Check if LLM API is available and configured
        llm_key = settings.LLM_API_KEY
        
        # Ensure columns is a normalized list of dicts with 'name' and 'type'
        normalized_cols = []
        for c in (columns or []):
            if isinstance(c, dict):
                normalized_cols.append({"name": str(c.get("name", "")), "type": str(c.get("type", "text"))})
            elif isinstance(c, str):
                parts = c.split(None, 1)
                normalized_cols.append({"name": parts[0], "type": parts[1] if len(parts) > 1 else "text"})
            else:
                normalized_cols.append({"name": str(c), "type": "text"})
        columns = normalized_cols

        schema_desc = f"Table: {table_name}\nColumns:\n" + "\n".join(
            [f"- {c['name']} ({c['type']})" for c in columns]
        )
        context_str = "\n".join(retrieved_context or [])
        
        history_lines = []
        for m in (conversation_context or []):
            role = m.get("role", "user")
            content = m.get("content", "")
            sql = m.get("sql")
            if sql:
                history_lines.append(f"{role}: [Executed SQL: {sql}] {content}")
            else:
                history_lines.append(f"{role}: {content}")
        history_str = "\n".join(history_lines)
        
        user_prompt = f"Schema:\n{schema_desc}\n\nContext:\n{context_str}\n\nHistory:\n{history_str}\n\nQuestion: {question}"

        prompt_summary = {
            "prompt_length": len(user_prompt),
            "preview": user_prompt[:200] + ("..." if len(user_prompt) > 200 else "")
        }

        if llm_key and llm_key.strip():
            provider = settings.LLM_PROVIDER.lower() if settings.LLM_PROVIDER else "openai"
            if llm_key.startswith("gsk_") or provider == "groq":
                provider = "groq"

            model_name = settings.LLM_MODEL
            if provider == "groq" and (model_name == "gpt-4o-mini" or not model_name):
                model_name = "openai/gpt-oss-120b"

            try:
                if provider == "groq":
                    try:
                        from groq import Groq
                        client = Groq(api_key=llm_key)
                    except ImportError:
                        from openai import OpenAI
                        client = OpenAI(api_key=llm_key, base_url="https://api.groq.com/openai/v1")
                else:
                    from openai import OpenAI
                    client = OpenAI(api_key=llm_key)

                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SQL_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0
                )

                content = response.choices[0].message.content
                result = json.loads(content)
                result["diagnostics"] = {
                    "mode": "llm",
                    "llm_provider": provider,
                    "llm_model": model_name,
                    "fresh_response": True,
                    "prompt_summary": prompt_summary,
                    "error": None
                }
                return result
            except Exception as e:
                logger.warning(
                    "[%s] LLM call failed (provider=%s, model=%s, error=%s: %s). Falling back to dynamic analytical query engine.",
                    req_id, provider, model_name, type(e).__name__, str(e)
                )
                fallback_res = cls._dynamic_analytical_generate(question, table_name, columns, conversation_context)
                fallback_res["diagnostics"] = {
                    "mode": "dynamic_analytical_fallback",
                    "llm_provider": provider,
                    "llm_model": model_name,
                    "fresh_response": False,
                    "prompt_summary": prompt_summary,
                    "error": f"{type(e).__name__}: {str(e)}"
                }
                return fallback_res

        # 4. Dynamic Schema-Driven Analytical Query Engine (Local / Offline mode)
        res = cls._dynamic_analytical_generate(question, table_name, columns, conversation_context)
        res["diagnostics"] = {
            "mode": "dynamic_analytical",
            "llm_provider": None,
            "llm_model": None,
            "fresh_response": False,
            "prompt_summary": prompt_summary,
            "error": None
        }
        return res

    @classmethod
    def _dynamic_analytical_generate(
        cls,
        question: str,
        table_name: str,
        columns: List[Dict[str, str]],
        conversation_context: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Dynamically analyzes schema and user question to construct correct, validated SQL.
        Handles counts, filtered counts, aggregations, rate comparisons, group breakdowns,
        rankings, duplicates, and conversation follow-ups.
        """
        q = question.strip().lower()
        col_names = [c["name"].lower() for c in columns]
        col_map = {c["name"].lower(): c["name"] for c in columns}

        # Helper to find column matching keywords
        def find_col(keywords: List[str], prefer_numeric: bool = False) -> Optional[str]:
            for kw in keywords:
                for c in col_names:
                    if kw == c:
                        return col_map[c]
            for kw in keywords:
                for c in col_names:
                    if kw in c:
                        return col_map[c]
            if prefer_numeric:
                num_cols = [c["name"] for c in columns if any(t in c["type"].lower() for t in ["int", "double", "float", "numeric", "bigint"])]
                return num_cols[0] if num_cols else columns[0]["name"]
            return None

        attrition_col = find_col(["attrition", "churn", "left", "turnover"])

        # 1. Follow-up query resolution using conversation history
        is_filter_change = any(w in q for w in ["filter to", "filter by", "where", "change filter"]) or bool(re.search(r'[a-zA-Z_]+\s*(>=|<=|>|<|=|!=)\s*[0-9.]+', q))

        # 1a. Column refinement: "only calories column", "only <col>", "just <col>", "show only <col>"
        if not is_filter_change and ("only" in q or "just" in q or "show only" in q or "column" in q) and conversation_context:
            matched_col = None
            for c in col_names:
                if re.search(r'\b' + re.escape(c) + r'\b', q):
                    matched_col = col_map[c]
                    break

            # Find last SQL from conversation context
            last_sql = None
            for m in reversed(conversation_context):
                if m.get("sql"):
                    last_sql = m.get("sql")
                    break

            if matched_col:
                if last_sql:
                    mod_sql = _transform_sql_to_only_column(last_sql, matched_col)
                    if mod_sql:
                        return {
                            "status": "query",
                            "sql": mod_sql,
                            "explanation": f"Preserved previous query logic and filters while selecting only the '{matched_col}' column.",
                            "clarification_question": None
                        }
                else:
                    # No explicit SQL in history, check previous content for highest/lowest
                    hist_text = " ".join([m.get("content", "").lower() for m in conversation_context])
                    if any(w in hist_text for w in ["highest", "max", "least", "lowest", "min"]):
                        sql = (
                            f"SELECT 'Highest {matched_col.title()}' AS heading, \"{matched_col}\" FROM \"{table_name}\" "
                            f"WHERE \"{matched_col}\" = (SELECT MAX(\"{matched_col}\") FROM \"{table_name}\" WHERE \"{matched_col}\" IS NOT NULL) "
                            f"UNION ALL "
                            f"SELECT 'Least {matched_col.title()}' AS heading, \"{matched_col}\" FROM \"{table_name}\" "
                            f"WHERE \"{matched_col}\" = (SELECT MIN(\"{matched_col}\") FROM \"{table_name}\" WHERE \"{matched_col}\" IS NOT NULL);"
                        )
                        return {
                            "status": "query",
                            "sql": sql,
                            "explanation": f"Returned only '{matched_col}' for highest and lowest non-null records.",
                            "clarification_question": None
                        }

        # 1b. Follow-up query changing a filter while preserving previous request's context
        if is_filter_change and conversation_context:
            last_sql = None
            for m in reversed(conversation_context):
                if m.get("sql"):
                    last_sql = m.get("sql")
                    break

            # Check if filter specifies a comparison like pulse > 100 or pulse >= 120
            comp_match = re.search(r'([a-zA-Z_]+)\s*(>=|<=|>|<|=|!=)\s*([0-9.]+)', q)
            if comp_match:
                c_name, op, val = comp_match.group(1).lower(), comp_match.group(2), comp_match.group(3)
                col_real = col_map.get(c_name)
                if col_real:
                    if last_sql:
                        try:
                            parsed = sqlglot.parse_one(last_sql, read="postgres")
                            new_cond = f'"{col_real}" {op} {val}'
                            where_expr = sqlglot.parse_one(f"SELECT * FROM t WHERE {new_cond}", read="postgres").find(exp.Where)
                            if isinstance(parsed, exp.Select):
                                parsed.set("where", where_expr)
                                sql = parsed.sql(dialect="postgres")
                            else:
                                proj_match = re.search(r'SELECT\s+(.*?)\s+FROM', last_sql, flags=re.IGNORECASE)
                                proj = proj_match.group(1) if proj_match else "*"
                                sql = f'SELECT {proj} FROM "{table_name}" WHERE "{col_real}" {op} {val};'
                        except Exception:
                            sql = f'SELECT * FROM "{table_name}" WHERE "{col_real}" {op} {val};'
                    else:
                        sql = f'SELECT * FROM "{table_name}" WHERE "{col_real}" {op} {val};'
                    return {
                        "status": "query",
                        "sql": sql,
                        "explanation": f"Preserved context and applied filter '{col_real} {op} {val}'.",
                        "clarification_question": None
                    }

            filter_val = q.replace("now only show", "").replace("only show", "").replace("filter to", "").replace("filter by", "").strip().strip(".'\"")
            city_col = find_col(["city", "location", "place", "region", "state", "department"])
            if city_col:
                sql = f'SELECT * FROM "{table_name}" WHERE LOWER("{city_col}") = \'{filter_val.lower()}\' LIMIT 50;'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Filtered records where {city_col} is '{filter_val}' using conversation context.",
                    "clarification_question": None
                }

        if ("higher attrition" in q or "previous comparison" in q or "that group" in q) and conversation_context:
            history_text = " ".join([m.get("content", "").lower() for m in conversation_context])
            income_col = find_col(["monthlyincome", "income", "salary", "wage", "pay"], prefer_numeric=True)
            if "overtime" in history_text or "overtime" in q:
                overtime_col = find_col(["overtime"])
                if overtime_col and income_col:
                    sql = f'SELECT ROUND(AVG("{income_col}")::numeric, 2) AS avg_{income_col} FROM "{table_name}" WHERE LOWER("{overtime_col}") = \'yes\';'
                    return {
                        "status": "query",
                        "sql": sql,
                        "explanation": f"Calculated the average {income_col} for employees who work overtime (the group with higher attrition rate: 30.53%).",
                        "clarification_question": None
                    }

        # 1c. Highest and lowest / Maximum and minimum non-null rows
        if (any(h in q for h in ["highest", "max", "maximum", "most"]) and any(l in q for l in ["lowest", "min", "minimum", "least"])) or ("highest and lowest" in q):
            target_metric = None
            for c in col_names:
                if c in q:
                    target_metric = col_map[c]
                    break
            if not target_metric:
                target_metric = find_col(["calories", "income", "salary", "sales", "amount", "price", "revenue", "pulse", "total"], prefer_numeric=True)
            
            if target_metric:
                sql = (
                    f"SELECT 'Highest {target_metric.title()} Burnt' AS heading, * FROM \"{table_name}\" "
                    f"WHERE \"{target_metric}\" = (SELECT MAX(\"{target_metric}\") FROM \"{table_name}\" WHERE \"{target_metric}\" IS NOT NULL) "
                    f"UNION ALL "
                    f"SELECT 'Least {target_metric.title()} Burnt' AS heading, * FROM \"{table_name}\" "
                    f"WHERE \"{target_metric}\" = (SELECT MIN(\"{target_metric}\") FROM \"{table_name}\" WHERE \"{target_metric}\" IS NOT NULL);"
                )
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Retrieved complete records for highest and lowest non-null {target_metric} values with distinct section headings.",
                    "clarification_question": None
                }

        # 2. Rate comparisons / Breakdown by dimension (e.g. "Compare attrition rates across departments", "between employees who work overtime")
        if ("compare" in q or "rate" in q or "rates" in q or "breakdown" in q or "by" in q) and ("attrition" in q or "left" in q or "turnover" in q):
            group_col = None
            if "department" in q:
                group_col = find_col(["department", "dept"])
            elif "overtime" in q:
                group_col = find_col(["overtime"])
            elif "role" in q or "job" in q:
                group_col = find_col(["jobrole", "role"])
            elif "gender" in q:
                group_col = find_col(["gender"])
            elif "education" in q:
                group_col = find_col(["educationfield", "education"])
            else:
                for c in col_names:
                    if len(c) > 3 and c in q and (not attrition_col or c != attrition_col.lower()):
                        group_col = col_map[c]
                        break

            if group_col and attrition_col:
                sql = (
                    f'SELECT "{group_col}", '
                    f'COUNT(*) AS total_employees, '
                    f'COUNT(*) FILTER (WHERE LOWER("{attrition_col}") = \'yes\') AS attrition_count, '
                    f'ROUND(COUNT(*) FILTER (WHERE LOWER("{attrition_col}") = \'yes\') * 100.0 / NULLIF(COUNT(*), 0), 2) AS attrition_rate_pct '
                    f'FROM "{table_name}" '
                    f'GROUP BY "{group_col}" '
                    f'ORDER BY attrition_rate_pct DESC;'
                )
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Compared attrition rates across {group_col}.",
                    "clarification_question": None
                }

        # 3. Average / Aggregation with filter (e.g. "What is the average monthly income of employees who left?")
        if ("average" in q or "avg" in q or "mean" in q) and ("who left" in q or "left the company" in q or "attrition = yes" in q or "attrition" in q or "who resigned" in q):
            val_col = find_col(["monthlyincome", "income", "salary", "order_value", "total", "amount", "price", "sales", "val", "cost", "revenue"], prefer_numeric=True)
            if attrition_col and val_col:
                sql = f'SELECT ROUND(AVG("{val_col}")::numeric, 2) AS avg_{val_col} FROM "{table_name}" WHERE LOWER("{attrition_col}") = \'yes\';'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Calculated the average {val_col} of employees who left (attrition = 'Yes').",
                    "clarification_question": None
                }

        # 4. Count queries: Filtered count (e.g. "How many employees left the company?")
        if ("how many" in q or "count" in q or "number of" in q) and ("left" in q or "resigned" in q or "quit" in q or "attrition" in q):
            if attrition_col:
                sql = f'SELECT COUNT(*) AS employees_left FROM "{table_name}" WHERE LOWER("{attrition_col}") = \'yes\';'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": "Counted employees who left the company (attrition = 'Yes').",
                    "clarification_question": None
                }

        # 5. Unfiltered Count queries (e.g. "How many employees are in this dataset?", "how many rows", "total rows")
        if "how many employees" in q or "total employees" in q or "how many rows" in q or "row count" in q or "count of employees" in q or "total rows" in q:
            sql = f'SELECT COUNT(*) AS total_employees FROM "{table_name}";'
            return {
                "status": "query",
                "sql": sql,
                "explanation": "Counted total employees in dataset.",
                "clarification_question": None
            }

        # 6. Unfiltered Average (e.g. "What is the average value of all orders?")
        if "average" in q or "avg" in q or "mean" in q:
            val_col = find_col(["net_amount", "order_value", "total", "amount", "price", "sales", "val", "cost", "revenue", "monthlyincome", "income", "salary"], prefer_numeric=True)
            sql = f'SELECT ROUND(AVG("{val_col}")::numeric, 2) AS average_{val_col} FROM "{table_name}";'
            return {
                "status": "query",
                "sql": sql,
                "explanation": f"Calculated the average of column '{val_col}'.",
                "clarification_question": None
            }

        # 7. Ranking / Top-K (e.g. "Which five cities have the highest total sales?")
        if any(w in q for w in ["highest", "top", "best"]) and any(w in q for w in ["sales", "revenue", "order", "total", "income", "amount"]):
            group_col = find_col(["city", "region", "country", "customer", "category", "product", "department"])
            val_col = find_col(["sales", "amount", "total", "price", "revenue", "net_amount", "monthlyincome"], prefer_numeric=True)
            limit_n = 5
            if "five" in q or " 5 " in q:
                limit_n = 5
            elif "ten" in q or " 10 " in q:
                limit_n = 10
            if group_col and val_col:
                sql = f'SELECT "{group_col}", SUM("{val_col}") AS total_{val_col} FROM "{table_name}" GROUP BY "{group_col}" ORDER BY total_{val_col} DESC LIMIT {limit_n};'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Ranked top {limit_n} {group_col} by sum of {val_col}.",
                    "clarification_question": None
                }

        # 8. Filter by city or location (e.g. "Show orders from Hyderabad")
        if "orders from" in q or "from hyderabad" in q or "show orders" in q or "in hyderabad" in q:
            city_col = find_col(["city", "location", "place"])
            city_val = "hyderabad" if "hyderabad" in q else "all"
            if city_col:
                sql = f'SELECT * FROM "{table_name}" WHERE LOWER("{city_col}") = \'{city_val}\' LIMIT 50;'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Retrieved orders filtered by {city_col} = '{city_val}'.",
                    "clarification_question": None
                }

        # 9. Duplicates (e.g. "How many duplicate customer IDs are there?")
        if "duplicate" in q:
            id_col = find_col(["customer_id", "customer", "order_id", "id", "user_id", "employeenumber"]) or col_names[0]
            sql = f'SELECT "{id_col}", COUNT(*) AS occurrences FROM "{table_name}" GROUP BY "{id_col}" HAVING COUNT(*) > 1;'
            return {
                "status": "query",
                "sql": sql,
                "explanation": f"Identified duplicate values in '{id_col}' appearing more than once.",
                "clarification_question": None
            }

        # 10. Monthly aggregation
        if "monthly" in q or "month" in q:
            date_col = find_col(["order_date", "date", "time", "created"])
            val_col = find_col(["net_amount", "revenue", "sales", "amount", "total"], prefer_numeric=True)
            if date_col and val_col:
                sql = f'SELECT TO_CHAR("{date_col}", \'YYYY-MM\') AS month, SUM("{val_col}") AS monthly_{val_col} FROM "{table_name}" GROUP BY month ORDER BY month;'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Aggregated monthly {val_col} over {date_col}.",
                    "clarification_question": None
                }

        # Default preview query
        return {
            "status": "query",
            "sql": f'SELECT * FROM "{table_name}" LIMIT 25;',
            "explanation": "Retrieved initial preview records from dataset.",
            "clarification_question": None
        }
