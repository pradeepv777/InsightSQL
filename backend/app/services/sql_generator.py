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
   - When the user refers to "table", "the table", "data", "dataset", or has a typo like "tablw", ALWAYS map it to the active table name provided in the schema. Do NOT ask for clarification if there is an available table.
4. If the question is truly ambiguous (such as an undefined quarter or missing business metric not in the dataset), return status: "clarification_needed" with a polite clarification_question and explanation.
5. Clarification Affirmation & Confirmation:
   - If the previous assistant message asked for clarification (such as confirming a table name or column), and the user responds with an affirmative confirmation (e.g., "yes", "correct", "yes it is correct", "yeah", "confirm", "ok", "sure", "do it", or confirms/quotes the table name):
     * You MUST NOT ask for clarification again.
     * You MUST immediately accept the confirmation, resolve the question to the clarified entity/table, and generate the final SELECT query.
6. Multi-turn Follow-up Handling:
   - When conversation history is present and the user asks a follow-up request (e.g., "only <column> column", "now only show X", "filter to Y", "order by Z", "also include W"):
     * ALWAYS preserve the previous query's core logic, filters, calculations, subqueries, and table references.
     * Treat follow-ups as natural conversational refinements, NEVER as a conflict. Do NOT ask for clarification when modifying columns or filters.
     * If the follow-up specifies a subset of columns (e.g. "only salary column", "only calories column"):
       - NEVER revert to an unfiltered table dump (like SELECT col FROM table).
       - You MUST keep all CTEs, WHERE filters, MAX/MIN subqueries, and UNION ALL branches from the previous query in history.
       - Only modify the SELECT projections to output the requested column(s) (while retaining any heading/label column if branches were unioned).
     * If the follow-up changes or narrows a filter, keep the previously selected columns and apply the new/updated filter to the same table.
7. When the user asks for separate tables or subsets (such as highest and lowest values):
   - You MUST use a UNION ALL query where the FIRST column in SELECT is named 'heading' (e.g., SELECT 'Highest <Metric>' AS heading, ... UNION ALL SELECT 'Lowest <Metric>' AS heading, ...) so the results can be grouped into separate named sections.
8. Return JSON format:
{
  "status": "query" | "clarification_needed",
  "sql": "SELECT ...",
  "explanation": "Brief description of the query calculation",
  "clarification_question": null or "Question text"
}
"""

SQL_REPAIR_SYSTEM_PROMPT = """You are a senior PostgreSQL engineer performing executable-query self-correction.
A previously generated SELECT query failed during execution. Root-cause the failure and return a corrected query.

Steps:
1. Compare the Failed SQL against the PostgreSQL error message and the table schema.
2. Identify the root cause (examples: a column referenced through a joined CTE that does not project it, a wrong column name, a type mismatch, or an ambiguous alias).
3. Produce a corrected single SELECT query that preserves the original analytical objective.

Rules:
1. ONLY produce a single SELECT statement (CTEs, subqueries, and UNION ALL are allowed).
2. Use the exact internal table name and column names provided in the schema. Never invent columns.
3. If you join or reference a CTE or alias, every selected column must exist in that CTE's own projection.
4. Keep the same result intent (same aggregations, filters, and comparisons) as the failed query.
5. If the query cannot be repaired without new information (e.g. the needed column truly does not exist), return status "unfixable" with sql: null.

Return JSON format:
{
  "status": "query" | "unfixable",
  "sql": "SELECT ..." or null,
  "explanation": "Root cause of the failure and description of the fix"
}
"""

def _transform_sql_to_only_column(sql: str, target_col: str) -> Optional[str]:
    """
    AST Query Transformer for Conversational Column Refinements (e.g. 'only calories column').
    
    Why this is technically hard and requires AST manipulation:
      - If the user previously asked for 'highest and lowest calories', the previous SQL query
        contains CTEs, WHERE subqueries, and UNION ALL branches.
      - A naive approach would generate `SELECT calories FROM table` (destroying all previous filters!).
      - This helper parses the existing query with `sqlglot`, finds only top-level SELECT projections
        (ignoring subqueries inside WHERE or CTE definitions), preserves section heading aliases,
        and swaps the data columns for the requested `target_col`.
    """
    try:
        parsed = sqlglot.parse_one(sql, read="postgres")
        heading_names = {
            "heading", "section", "table", "category", "type", "label",
            "title", "subset", "tag", "group", "result_table"
        }
        
        for select in parsed.find_all(exp.Select):
            # Skip inner subqueries inside WHERE clauses or CTE definitions to preserve their internal logic
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
                # Retain heading / title / label expressions so multi-section UI grouping remains intact
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
    """
    Natural Language to PostgreSQL Query Generation Engine.
    
    Integrates Groq LPU inference (ultra-low latency open-weights models) or OpenAI,
    backed by a deterministic dynamic analytical query engine when offline.
    """

    @classmethod
    def generate_sql(
        cls,
        question: str,
        table_name: str,
        columns: List[Dict[str, str]],
        retrieved_context: Optional[List[str]] = None,
        conversation_context: Optional[List[Dict[str, str]]] = None,
        request_id: Optional[str] = None,
        schema_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Translates a natural language question into PostgreSQL SQL:
          1. Detects business ambiguities requiring clarification.
          2. Recognizes user confirmations (e.g. 'yes', 'confirm') after clarification.
          3. Formats prompt with Schema, RAG Context, and Conversation History (including past SQL).
          4. Calls Groq/OpenAI with temperature=0.0 and JSON mode enforcement.
          5. Falls back to dynamic analytical engine if LLM fails or API key is absent.
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

        # 2. Check for direct table inspection queries (e.g. 'select * from table', 'select * from tablw', 'show all rows')
        # tablw - delibrate mistake 
        if re.search(r'^(?:select\s+\*\s+from\s+(?:the\s+)?(?:table|tablw|dataset|data)\s*;?|show\s+(?:all\s+)?(?:the\s+)?(?:rows|table|tablw|data|dataset)\s*;?)$', q_lower):
            return {
                "status": "query",
                "sql": f'SELECT * FROM "{table_name}" LIMIT 50;',
                "explanation": f"Selects records from the active table '{table_name}'.",
                "clarification_question": None,
                "diagnostics": {
                    "mode": "rule_based",
                    "llm_provider": None,
                    "llm_model": None,
                    "fresh_response": False,
                    "prompt_summary": "Direct table inspection requested"
                }
            }

        # 3. Check for user confirmation/affirmation following a clarification request
        affirmation_phrases = {
            "yes", "correct", "yes it is correct", "yeah", "yep", "sure", "ok", "confirm",
            "yes please", "do it", "proceed", "right", "thats correct", "that is correct",
            "yes correct", "yes exactly", "it is correct"
        }
        clean_q = re.sub(r'[^a-zA-Z0-9_\s]', '', q_lower).strip()
        is_affirmation = (
            clean_q in affirmation_phrases
            or clean_q.startswith("yes ")
            or "it is correct" in clean_q
            or "thats correct" in clean_q
            or (clean_q.startswith("yes") and table_name.lower() in clean_q)
            or (clean_q.startswith("yes") and any(clean_q.endswith(table_name.lower()[:i]) for i in range(8, len(table_name) + 1)))
        )

        if is_affirmation and conversation_context:
            prev_asst = conversation_context[-1] if conversation_context else None
            prev_content = (prev_asst.get("content") or "").lower() if prev_asst else ""
            if "clarif" in prev_content or "did you mean" in prev_content or "misspelled" in prev_content or "confirm" in prev_content:
                orig_user_q = None
                for m in reversed(conversation_context[:-1]):
                    if m.get("role") == "user":
                        orig_user_q = m.get("content", "")
                        break
                
                if orig_user_q:
                    if re.search(r'select\s+\*\s+from\s+(?:the\s+)?(?:table|tablw|dataset|data)', orig_user_q, re.I):
                        return {
                            "status": "query",
                            "sql": f'SELECT * FROM "{table_name}" LIMIT 50;',
                            "explanation": f"Confirmed query on table '{table_name}'.",
                            "clarification_question": None,
                            "diagnostics": {
                                "mode": "rule_based",
                                "llm_provider": None,
                                "llm_model": None,
                                "fresh_response": False,
                                "prompt_summary": "Clarification confirmation resolved"
                            }
                        }
                    question = re.sub(r'\b(the\s+)?(tablw|table|dataset|data)\b', table_name, orig_user_q, flags=re.I)
                    q_lower = question.strip().lower()

        # Correct obvious typos in question before sending to LLM
        question = re.sub(r'\b(from\s+)(?:the\s+)?tablw\b', rf'\1"{table_name}"', question, flags=re.I)
        q_lower = question.strip().lower()

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

        if schema_override:
            schema_desc = schema_override
        else:
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

            model_name = settings.LLM_MODEL or "openai/gpt-oss-20b"
            models_to_try = [model_name]
            if provider == "groq":
                if "120b" in model_name and "openai/gpt-oss-20b" not in models_to_try:
                    models_to_try.append("openai/gpt-oss-20b")
                elif "20b" in model_name and "openai/gpt-oss-120b" not in models_to_try:
                    models_to_try.append("openai/gpt-oss-120b")

            client = None
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

            last_error = None
            for current_model in models_to_try:
                try:
                    response = client.chat.completions.create(
                        model=current_model,
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
                        "llm_model": current_model,
                        "fresh_response": True,
                        "prompt_summary": prompt_summary,
                        "error": None
                    }
                    return result
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "[%s] LLM call failed on model %s (provider=%s, error=%s: %s).",
                        req_id, current_model, provider, type(e).__name__, str(e)
                    )

            logger.warning(
                "[%s] All LLM models failed. Falling back to dynamic analytical query engine. Error: %s",
                req_id, str(last_error)
            )
            fallback_res = cls._dynamic_analytical_generate(question, table_name, columns, conversation_context)
            err_msg = f"{type(last_error).__name__}: {str(last_error)}" if last_error else "LLM unavailable"
            fallback_res["diagnostics"] = {
                "mode": "dynamic_analytical_fallback",
                "llm_provider": provider,
                "llm_model": model_name,
                "fresh_response": False,
                "prompt_summary": prompt_summary,
                "error": err_msg
            }
            if fallback_res.get("explanation") == "Retrieved initial preview records from dataset.":
                fallback_res["explanation"] = f"LLM rate limit reached ({type(last_error).__name__}). Defaulting to initial dataset preview."
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
    def regenerate_with_execution_error(
        cls,
        question: str,
        table_name: str,
        columns: List[Dict[str, str]],
        failed_sql: str,
        db_error: str,
        conversation_context: Optional[List[Dict[str, str]]] = None,
        request_id: Optional[str] = None,
        schema_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Automated Self-Correction Engine (Feedback-Driven Query Repair):
          - LLMs occasionally hallucinate a non-existent column, miss a CTE projection, or
            produce a datatype mismatch (e.g. comparing string to int).
          - Rather than failing the user request, this method re-prompts the model using
            `SQL_REPAIR_SYSTEM_PROMPT` containing:
              1. The original question
              2. The exact table schema
              3. The failed SQL query
              4. The exact PostgreSQL runtime error message
          - Returns status: "repaired" with the corrected SQL, or "unfixable" if the needed
            attribute is fundamentally absent from the schema.
        """
        req_id = request_id or "local"
        llm_key = settings.LLM_API_KEY

        if not (llm_key and llm_key.strip()):
            return {
                "status": "no_llm",
                "sql": None,
                "explanation": "No LLM API key configured; execution self-correction unavailable.",
                "diagnostics": {"mode": "execution_repair", "fresh_response": False}
            }

        # Normalize columns for the schema description
        normalized_cols = []
        for c in (columns or []):
            if isinstance(c, dict):
                normalized_cols.append({"name": str(c.get("name", "")), "type": str(c.get("type", "text"))})
            else:
                normalized_cols.append({"name": str(c), "type": "text"})
        columns = normalized_cols

        if schema_override:
            schema_desc = schema_override
        else:
            schema_desc = f"Table: {table_name}\nColumns:\n" + "\n".join(
                [f"- {c['name']} ({c['type']})" for c in columns]
            )
        history_lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in (conversation_context or [])]
        history_str = "\n".join(history_lines)

        repair_prompt = (
            f"Original Question: {question}\n\n"
            f"Schema:\n{schema_desc}\n\n"
            f"History:\n{history_str}\n\n"
            f"Failed SQL:\n{failed_sql}\n\n"
            f"PostgreSQL Error:\n{db_error}\n\n"
            f"Return the corrected query as JSON."
        )

        provider = settings.LLM_PROVIDER.lower() if settings.LLM_PROVIDER else "openai"
        if llm_key.startswith("gsk_") or provider == "groq":
            provider = "groq"

        model_name = settings.LLM_MODEL or "openai/gpt-oss-120b"

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
                    {"role": "system", "content": SQL_REPAIR_SYSTEM_PROMPT},
                    {"role": "user", "content": repair_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            sql = result.get("sql")
            status = result.get("status")
            if status == "query" and sql:
                final_status = "repaired"
            else:
                final_status = "unfixable"
                sql = None
            return {
                "status": final_status,
                "sql": sql,
                "explanation": result.get("explanation"),
                "diagnostics": {
                    "mode": "execution_repair",
                    "llm_provider": provider,
                    "llm_model": model_name,
                    "fresh_response": True,
                    "error": None
                }
            }
        except Exception as e:
            logger.warning(
                "[%s] Execution-error repair call failed (provider=%s, model=%s, error=%s: %s).",
                req_id, provider, model_name, type(e).__name__, str(e)
            )
            return {
                "status": "repair_failed",
                "sql": None,
                "explanation": f"Repair attempt failed: {type(e).__name__}: {str(e)}",
                "diagnostics": {
                    "mode": "execution_repair",
                    "llm_provider": provider,
                    "llm_model": model_name,
                    "fresh_response": False,
                    "error": f"{type(e).__name__}: {str(e)}"
                }
            }

    @classmethod
    def _dynamic_analytical_generate(
        cls,
        question: str,
        table_name: str,
        columns: List[Dict[str, str]],
        conversation_context: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Deterministic Dynamic Analytical SQL Engine (Offline / Fallback Mode).
        
        Analyzes the column names, datatypes, and conversational intent to construct
        syntactically correct, validated PostgreSQL queries without requiring an active LLM:
          - Multi-turn column projection refinements (preserving previous CTEs/WHERE clauses)
          - Filter updates (comparison operators >=, <=, >, <, =, !=)
          - Highest / lowest boundary records using UNION ALL with section headings
          - Dimension rate comparisons with conditional aggregation (`COUNT(*) FILTER (...)`)
          - Averages, top-K rankings, duplicate detections, and temporal grouping (`TO_CHAR`)
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

        if conversation_context and (
            re.search(r"\b(higher|highest|lower|lowest)\b", q) or
            any(w in q for w in ["previous comparison", "that group", "higher rate", "highest rate"])
        ):
            last_sql = next((m.get("sql") for m in reversed(conversation_context) if m.get("sql")), "")
            group_match = re.search(r'GROUP BY\s+"?([A-Za-z0-9_]+)"?', last_sql, re.IGNORECASE)
            prev_group_col = group_match.group(1) if group_match else None
            
            metric_col = next((col_map[c] for c in col_names if c in q and c != (prev_group_col or '').lower()), None)
            if not metric_col:
                metric_col = find_col(["income", "salary", "amount", "price", "sales", "revenue", "value", "cost"], prefer_numeric=True)

            if prev_group_col and metric_col:
                sql = (
                    f'SELECT ROUND(AVG("{metric_col}")::numeric, 2) AS avg_{metric_col} '
                    f'FROM "{table_name}" '
                    f'WHERE "{prev_group_col}" = ('
                    f'SELECT "{prev_group_col}" FROM "{table_name}" '
                    f'GROUP BY "{prev_group_col}" '
                    f'ORDER BY COUNT(*) DESC LIMIT 1);'
                )
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Calculated the average {metric_col} for the leading group from previous comparison.",
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
                    f"SELECT 'Highest {target_metric.title()}' AS heading, * FROM \"{table_name}\" "
                    f"WHERE \"{target_metric}\" = (SELECT MAX(\"{target_metric}\") FROM \"{table_name}\" WHERE \"{target_metric}\" IS NOT NULL) "
                    f"UNION ALL "
                    f"SELECT 'Least {target_metric.title()}' AS heading, * FROM \"{table_name}\" "
                    f"WHERE \"{target_metric}\" = (SELECT MIN(\"{target_metric}\") FROM \"{table_name}\" WHERE \"{target_metric}\" IS NOT NULL);"
                )
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Retrieved complete records for highest and lowest non-null {target_metric} values with distinct section headings.",
                    "clarification_question": None
                }

        # 2. Rate comparisons / Breakdown by dimension (e.g. "Compare rate across <dimension>")
        if any(w in q for w in ["compare", "rate", "rates", "breakdown", "proportion", "percentage"]):
            group_col = next((col_map[c] for c in col_names if len(c) > 2 and c in q), None)
            target_col = next((col_map[c] for c in col_names if len(c) > 2 and c in q and c != (group_col or '').lower()), None)
            
            if not group_col and target_col:
                group_col = next((col_map[c] for c in col_names if c != target_col.lower() and not any(t in c for t in ["id", "date"])), None)

            if group_col and target_col:
                rate_alias = f"{target_col.lower()}_rate_pct"
                cnt_alias = f"{target_col.lower()}_count"
                sql = (
                    f'SELECT "{group_col}", '
                    f'COUNT(*) AS total_records, '
                    f'COUNT(*) FILTER (WHERE LOWER(CAST("{target_col}" AS text)) IN (\'yes\', \'true\', \'1\', \'y\')) AS {cnt_alias}, '
                    f'ROUND(COUNT(*) FILTER (WHERE LOWER(CAST("{target_col}" AS text)) IN (\'yes\', \'true\', \'1\', \'y\')) * 100.0 / NULLIF(COUNT(*), 0), 2) AS {rate_alias} '
                    f'FROM "{table_name}" '
                    f'GROUP BY "{group_col}" '
                    f'ORDER BY {rate_alias} DESC;'
                )
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Compared {target_col} rates across {group_col}.",
                    "clarification_question": None
                }

        # 3. Average / Aggregation with filter (e.g. "What is the average <metric> of records where ...")
        if any(w in q for w in ["average", "avg", "mean"]) and any(w in q for w in ["where", "who", "with", "left", "yes", "true", "churn"]):
            val_col = next((col_map[c] for c in col_names if c in q), None)
            if not val_col:
                val_col = find_col(["income", "salary", "amount", "price", "sales", "revenue", "value", "cost"], prefer_numeric=True)
            cond_col = next((col_map[c] for c in col_names if len(c) > 2 and (c in q or (c in ["attrition", "churn"] and any(k in q for k in ["left", "resigned", "quit", "churn"]))) and c != (val_col or '').lower()), None)
            if cond_col and val_col:
                sql = f'SELECT ROUND(AVG("{val_col}")::numeric, 2) AS avg_{val_col} FROM "{table_name}" WHERE LOWER(CAST("{cond_col}" AS text)) IN (\'yes\', \'true\', \'1\', \'y\');'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Calculated the average {val_col} for records matching condition.",
                    "clarification_question": None
                }

        # 4. Filtered count queries (e.g. "How many <items> where ...")
        if any(w in q for w in ["how many", "count", "number of"]) and any(w in q for w in ["where", "left", "resigned", "quit", "churn", "returned", "cancelled", "active"]):
            cond_col = next((col_map[c] for c in col_names if len(c) > 2 and (c in q or (c in ["attrition", "churn"] and any(k in q for k in ["left", "resigned", "quit", "churn"])))), None)
            if cond_col:
                sql = f'SELECT COUNT(*) AS total_matching FROM "{table_name}" WHERE LOWER(CAST("{cond_col}" AS text)) IN (\'yes\', \'true\', \'1\', \'y\');'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Counted records where {cond_col} matches positive condition.",
                    "clarification_question": None
                }

        # 5. Total count queries (e.g. "How many rows in dataset?", "total rows", "how many items")
        if any(w in q for w in ["how many", "total", "count"]) and any(w in q for w in ["rows", "records", "items", "dataset", "people", "employees", "orders", "users", "customers"]):
            sql = f'SELECT COUNT(*) AS total_records FROM "{table_name}";'
            return {
                "status": "query",
                "sql": sql,
                "explanation": "Counted total records in dataset.",
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
            limit_n = 10 if ("ten" in q or " 10 " in q) else 5
            if group_col and val_col:
                sql = f'SELECT "{group_col}", SUM("{val_col}") AS total_{val_col} FROM "{table_name}" GROUP BY "{group_col}" ORDER BY total_{val_col} DESC LIMIT {limit_n};'
                return {
                    "status": "query",
                    "sql": sql,
                    "explanation": f"Ranked top {limit_n} {group_col} by sum of {val_col}.",
                    "clarification_question": None
                }

        # 8. Filter by city or location (e.g. "Show orders from Hyderabad", "Now only show Hyderabad")
        loc_match = re.search(r'\b(?:from|in|show|filter to|only show)\s+([a-zA-Z_\s]+?)(?:\s+orders|\s+customers|\s+records|\s*$)', q)
        if loc_match:
            loc_val = loc_match.group(1).strip()
            if loc_val and loc_val.lower() not in ["all", "table", "data", "dataset", "column", "columns"]:
                city_col = find_col(["city", "location", "place", "region", "state", "department"])
                if city_col:
                    sql = f'SELECT * FROM "{table_name}" WHERE LOWER("{city_col}") = \'{loc_val.lower()}\' LIMIT 50;'
                    return {
                        "status": "query",
                        "sql": sql,
                        "explanation": f"Retrieved records filtered by {city_col} = '{loc_val}'.",
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
