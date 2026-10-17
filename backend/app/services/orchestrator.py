import time
import uuid
import re
from typing import Dict, Any, List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import query_engine
from app.models.models import Dataset, DatasetTable, ColumnMetadata, Conversation, Message, QueryRun
from app.services.sql_policy import SQLPolicyValidator, SQLPolicyError
from app.services.sql_generator import SQLGenerator
from app.services.rag import RAGService
from app.core.logging import log_diagnostic_event, logger

def _format_cell_value(val: Any, col_name: str = "") -> str:
    """Consolidated number and currency formatter for analytical summaries."""
    if val is None:
        return "N/A"
    try:
        num = float(val)
        col = col_name.lower()
        if any(k in col for k in ["income", "salary", "amount", "price", "revenue", "cost"]):
            return f"${num:,.2f}"
        if num.is_integer() and any(k in col for k in ["count", "total", "rows", "occurrences"]):
            return f"{int(num):,}"
        if "." in str(val):
            return f"{num:,.2f}"
        return f"{int(num):,}"
    except (ValueError, TypeError):
        return str(val)

class QueryOrchestrator:
    """
    Core Pipeline Coordinator for InsightSQL.
    
    Architectural Lifecycle (12 Steps):
      1. Session & History Persistence: Guarantees user message is saved before external calls.
      2. Dataset Resolution: 5-tier fallback (explicit ID -> prior turn -> last SQL run -> schema match -> single ready dataset).
      3. Fast-Path Optimization: Bypasses LLM/DB for precomputed metadata & data dictionary terms.
      4. RAG Schema Retrieval: BM25 lexical ranking finds relevant table/column documentation.
      5. Multi-Turn Context Assembly: Injects previous assistant turns AND their executed SQL.
      6. LLM SQL Synthesis: Generates candidate query with deterministic JSON schema.
      7. Clarification Routing: Detects genuine business ambiguities without guessing.
      8. AST Security Sandboxing: sqlglot verifies single-SELECT, table allowlists, and no mutations.
      9. Bounded PostgreSQL Execution: Dedicated query engine enforces 5000ms statement_timeout.
     10. Runtime Self-Correction: Feeds Postgres runtime compiler errors back to LLM to auto-repair.
     11. Result Synthesis & Section Splitting: Formats numbers, auto-configures charts, splits UNION headings.
     12. Audit Trail & Diagnostics: Records QueryRun performance metrics and structured telemetry.
    """

    @classmethod
    def process_message(
        cls,
        db: Session,
        conversation_id: str,
        question: str,
        dataset_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        start_time = time.time()
        q_lower = question.strip().lower()

        # Telemetry tracker: captures pipeline state at each step for debugging and observability
        diag: Dict[str, Any] = {
            "request_id": request_id,
            "question": question,
            "dataset_id": None,
            "dataset_name": None,
            "table_name": None,
            "retrieved_docs": [],
            "prompt_summary": None,
            "llm_provider": None,
            "llm_model": None,
            "llm_fresh_response": False,
            "generation_mode": None,
            "proposed_sql": None,
            "sql_validation": None,
            "queried_postgres": False,
            "execution_status": "not_queried",
            "execution_ms": 0,
            "row_count": 0,
            "final_status": "in_progress"
        }
        
        # 1. Ensure conversation exists (creates an isolated thread if not already created)
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conv:
            conv = Conversation(id=conversation_id, title="Data Analysis")
            db.add(conv)
            db.commit()

        # 2. Record User Message immediately (ensures user question is persisted even if subsequent LLM/DB calls crash)
        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=question
        )
        db.add(user_msg)
        db.commit()

        # 3. Resolve active dataset via 4-tier fallback hierarchy:
        #    Tier A: Explicit dataset_ids passed by caller/frontend
        active_dataset = None
        if dataset_ids and len(dataset_ids) > 0:
            active_dataset = db.query(Dataset).filter(Dataset.id.in_(dataset_ids), Dataset.status == "ready").first()

        #    Tier B: Conversational memory — inherit dataset from the preceding turn in this conversation
        if not active_dataset:
            last_msg_with_ds = db.query(Message).filter(
                Message.conversation_id == conversation_id,
                Message.metadata_json.isnot(None)
            ).order_by(Message.created_at.desc()).first()
            if last_msg_with_ds and isinstance(last_msg_with_ds.metadata_json, dict):
                prev_ds_id = last_msg_with_ds.metadata_json.get("dataset_id")
                if prev_ds_id:
                    active_dataset = db.query(Dataset).filter(Dataset.id == prev_ds_id, Dataset.status == "ready").first()

        #    Tier C: Recent QueryRun history — inspect the table name executed in the last successful query
        if not active_dataset:
            last_qr = db.query(QueryRun).filter(
                QueryRun.conversation_id == conversation_id,
                QueryRun.status == "success"
            ).order_by(QueryRun.created_at.desc()).first()
            if last_qr and last_qr.generated_sql:
                for ds in db.query(Dataset).filter(Dataset.status == "ready").order_by(Dataset.created_at.desc()).all():
                    for t in ds.tables:
                        if t.table_name.lower() in last_qr.generated_sql.lower():
                            active_dataset = ds
                            break
                    if active_dataset:
                        break

        #    Tier D: Schema lexical matching — score ready datasets by column name occurrences in the user prompt
        if not active_dataset:
            all_ready = db.query(Dataset).filter(Dataset.status == "ready").order_by(Dataset.created_at.desc()).all()
            if all_ready:
                best_dataset = None
                best_match_count = 0
                for ds in all_ready:
                    dt = db.query(DatasetTable).filter(DatasetTable.dataset_id == ds.id).first()
                    if dt:
                        match_count = 0
                        for col in dt.columns:
                            c_name = col.column_name.lower()
                            if len(c_name) > 2 and re.search(r'\b' + re.escape(c_name) + r'\b', q_lower):
                                match_count += 2
                        if match_count > best_match_count:
                            best_match_count = match_count
                            best_dataset = ds
                if best_dataset and best_match_count >= 2:
                    active_dataset = best_dataset

        #    Tier E: Default to the most recently uploaded ready dataset
        if not active_dataset:
            active_dataset = db.query(Dataset).filter(Dataset.status == "ready").order_by(Dataset.created_at.desc()).first()

        if not active_dataset:
            ans = "No dataset is currently uploaded. Please upload a CSV, Excel, Parquet, or JSON file first."
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "no_dataset"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "visualization": None,
                "status": "no_dataset",
                "clarification_question": None,
                "warnings": ["Please upload a dataset using the '+' button to begin analysis."]
            }

        diag["dataset_id"] = str(active_dataset.id)
        diag["dataset_name"] = active_dataset.display_name

        # Find tables for active dataset
        all_ds_tables = db.query(DatasetTable).filter(DatasetTable.dataset_id == active_dataset.id).all()
        if not all_ds_tables:
            ans = f"Dataset '{active_dataset.display_name}' has no available tables."
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "error"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "visualization": None,
                "status": "error",
                "clarification_question": None,
                "warnings": []
            }

        ds_table = all_ds_tables[0]
        table_name = ds_table.table_name
        diag["table_name"] = table_name

        # If multi-table relational dataset, build full multi-table schema and allow all tables
        if len(all_ds_tables) > 1:
            allowed_tables = {t.table_name for t in all_ds_tables}
            schema_blocks = []
            col_list = []
            for dt in all_ds_tables:
                dt_cols = db.query(ColumnMetadata).filter(ColumnMetadata.dataset_table_id == dt.id).all()
                c_lines = [f"- {c.column_name} ({c.database_type})" for c in dt_cols]
                schema_blocks.append(f"Table: {dt.table_name}\nColumns:\n" + "\n".join(c_lines))
                for c in dt_cols:
                    col_list.append({"name": c.column_name, "type": c.database_type, "table": dt.table_name})
            multi_table_schema = "\n\n".join(schema_blocks)
            columns_meta = db.query(ColumnMetadata).filter(ColumnMetadata.dataset_table_id == ds_table.id).all()
        else:
            allowed_tables = {table_name}
            columns_meta = db.query(ColumnMetadata).filter(ColumnMetadata.dataset_table_id == ds_table.id).all()
            col_list = [{"name": c.column_name, "type": c.database_type} for c in columns_meta]
            multi_table_schema = None

        # 4. Fast-Path Optimization: Check for direct metadata profile questions.
        #    Bypasses LLM token spend and DB latency by reading cached ingestion profiles.
        if "how many rows and columns" in q_lower or ("rows and columns" in q_lower and "how many" in q_lower):
            ans = f"The dataset '{active_dataset.display_name}' contains {ds_table.row_count:,} rows and {len(col_list)} columns: {', '.join([c['name'] for c in col_list])}."
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "success"
            diag["execution_status"] = "metadata_lookup"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": ["metric", "value"],
                "rows": [["Rows", ds_table.row_count], ["Columns", len(col_list)]],
                "row_count": 2,
                "visualization": None,
                "status": "success",
                "clarification_question": None,
                "warnings": []
            }

        # Fast-Path: Missing-value profiles cached during ingestion in ColumnMetadata.profile_json
        if "missing-value counts" in q_lower or "missing value" in q_lower or "null count" in q_lower or "show missing" in q_lower:
            rows = []
            for col in columns_meta:
                prof = col.profile_json or {}
                null_cnt = prof.get("null_count", 0)
                rows.append([col.column_name, null_cnt, f"{(null_cnt / max(ds_table.row_count, 1) * 100):.1f}%"])
            ans = f"Here are the missing-value counts for all columns in '{active_dataset.display_name}':"
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "success"
            diag["execution_status"] = "metadata_lookup"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": ["column_name", "missing_count", "missing_percentage"],
                "rows": rows,
                "row_count": len(rows),
                "visualization": {"type": "bar", "x": "column_name", "y": "missing_count"},
                "status": "success",
                "clarification_question": None,
                "warnings": []
            }

        # 5. Fast-Path: Data dictionary lookup via RAG business definitions (e.g., "what does attrition mean?")
        if "what does" in q_lower and "mean" in q_lower:
            target = q_lower.replace("what does", "").replace("mean", "").strip().strip("`'\"? ")
            col_def = RAGService.get_column_definition(db, target, [active_dataset.id])
            if col_def:
                ans = f"Data definition for `{target}`: {col_def}"
            else:
                ans = f"No documentation or definition was found for `{target}` in the uploaded dataset dictionary."
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "success"
            diag["execution_status"] = "rag_definition_lookup"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "visualization": None,
                "status": "success",
                "clarification_question": None,
                "warnings": []
            }

        # 6. RAG Context Retrieval: Lexically indexes schemas and business definitions via BM25,
        #    strictly filtered to the authorized active dataset to ground the LLM prompt.
        retrieved_docs = RAGService.retrieve_context(db, question, [active_dataset.id], top_k=4)
        diag["retrieved_docs"] = [
            {"id": str(d.get("id")), "score": d.get("score"), "type": d.get("type")}
            for d in retrieved_docs
        ]
        rag_context = [d["content"] for d in retrieved_docs]

        # 7. Multi-Turn Context Assembly: Load the last 6 messages.
        #    Crucial: Attaches previously executed SQL to assistant messages so follow-up queries
        #    (e.g., "now only show calories", "filter to Hyderabad") preserve previous CTEs and WHERE clauses.
        recent_messages = db.query(Message).filter(
            Message.conversation_id == conversation_id,
            Message.id != user_msg.id
        ).order_by(Message.created_at.desc()).limit(6).all()
        
        conv_context = []
        for m in reversed(recent_messages):
            entry = {"role": m.role, "content": m.content or ""}
            if m.role == "assistant" and m.metadata_json and isinstance(m.metadata_json, dict) and m.metadata_json.get("sql"):
                entry["sql"] = m.metadata_json["sql"]
            conv_context.append(entry)

        # 8. SQL Generation: Calls Groq/OpenAI with schema, context, and conversation turns.
        #    Falls back to deterministic rule-based engine if offline or rate-limited.
        sql_plan = SQLGenerator.generate_sql(
            question=question,
            table_name=table_name,
            columns=col_list,
            retrieved_context=rag_context,
            conversation_context=conv_context,
            request_id=request_id,
            schema_override=multi_table_schema
        )

        plan_diag = sql_plan.get("diagnostics", {})
        diag["prompt_summary"] = plan_diag.get("prompt_summary")
        diag["llm_provider"] = plan_diag.get("llm_provider")
        diag["llm_model"] = plan_diag.get("llm_model")
        diag["llm_fresh_response"] = plan_diag.get("fresh_response", False)
        diag["generation_mode"] = plan_diag.get("mode", "unknown")
        diag["proposed_sql"] = sql_plan.get("sql")

        # 8a. Clarification Guardrail: If query is genuinely ambiguous (e.g. unspecified fiscal quarter),
        #     ask user politely rather than guessing and returning misleading analytical numbers.
        if sql_plan.get("status") == "clarification_needed":
            explanation = sql_plan.get("explanation") or "Clarification is needed before running this query."
            clarification = sql_plan.get("clarification_question") or explanation
            if clarification and clarification.lower() not in explanation.lower():
                full_content = f"{explanation}\nClarification Required: {clarification}"
            else:
                full_content = explanation
            ans = full_content
            asst_msg = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                metadata_json={"clarification_question": clarification, "status": "clarification_needed"}
            )
            db.add(asst_msg)
            db.commit()
            diag["final_status"] = "clarification_needed"
            diag["execution_status"] = "not_queried"
            log_diagnostic_event(diag)
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "sections": None,
                "visualization": None,
                "status": "clarification_needed",
                "clarification_question": clarification,
                "warnings": []
            }

        proposed_sql = sql_plan.get("sql")
        explanation = sql_plan.get("explanation", "Executed analytical query.")

        # 9. AST Security Sandboxing: Validates query against strict policies using sqlglot:
        #    - Restricts to a solitary SELECT statement (blocks DDL/DML, semicolon-chained attacks).
        #    - Blocks system schemas (pg_catalog, information_schema).
        #    - Restricts table access solely to tables in `allowed_tables`.
        validated_sql = None
        try:
            validated_sql = SQLPolicyValidator.validate_and_format(proposed_sql, allowed_tables)
            diag["sql_validation"] = "passed"
        except SQLPolicyError as spe:
            # Fallback resilience: If the LLM hallucinated an invalid syntax or unauthorized table,
            # attempt generation via the deterministic dynamic analytical query engine.
            if diag.get("generation_mode") == "llm":
                logger.warning(
                    f"LLM generated query was rejected ({spe.message}). Attempting dynamic analytical fallback."
                )
                try:
                    fallback_plan = SQLGenerator._dynamic_analytical_generate(question, table_name, col_list, conv_context)
                    fallback_sql = fallback_plan.get("sql")
                    if fallback_sql:
                        validated_sql = SQLPolicyValidator.validate_and_format(fallback_sql, allowed_tables)
                        proposed_sql = validated_sql
                        explanation = fallback_plan.get("explanation", explanation)
                        diag["sql_validation"] = "passed_dynamic_fallback"
                        diag["generation_mode"] = "llm_fallback_dynamic"
                except Exception:
                    validated_sql = None

            # If still rejected after fallback, record policy failure audit trail and return polite alert
            if not validated_sql:
                diag["sql_validation"] = f"rejected: {spe.message}"
                diag["final_status"] = "rejected"
                diag["execution_status"] = "not_queried"
                log_diagnostic_event(diag)
                ans = f"Security Policy Rejected Query: {spe.message}"
                asst_msg = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=ans,
                    metadata_json={
                        "sql": proposed_sql,
                        "dataset_id": str(active_dataset.id),
                        "table_name": table_name,
                        "error_category": spe.code
                    }
                )
                q_run = QueryRun(
                    conversation_id=conversation_id,
                    question=question,
                    generated_sql=proposed_sql,
                    status="rejected",
                    error_category=spe.code
                )
                db.add(asst_msg)
                db.add(q_run)
                db.commit()
                return {
                    "message_id": asst_msg.id,
                    "answer": ans,
                    "sql": proposed_sql,
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "sections": None,
                    "visualization": None,
                    "status": "rejected",
                    "clarification_question": None,
                    "warnings": [spe.message]
                }

        # 10. Bounded Execution & LLM Self-Correction Loop
        #     Uses dedicated `query_engine` configured with PostgreSQL session-level statement_timeout (5000ms).
        exec_start = time.time()
        diag["queried_postgres"] = True
        current_sql = validated_sql
        exec_last_error = None
        max_exec_retries = 1 if str(diag.get("generation_mode", "")).startswith("llm") else 0

        def _is_retryable_exec_error(err_str: str) -> bool:
            """
            Latency optimization: Skip self-correction retries for timeouts or client cancellations.
            If a query ran for 5000ms and timed out, repeating it would only waste more latency.
            """
            lowered = err_str.lower()
            return not ("canceling statement" in lowered or "statement_timeout" in lowered)

        try:
            for attempt in range(max_exec_retries + 1):
                try:
                    with query_engine.connect() as conn:
                        res = conn.execute(text(current_sql))
                        cols = list(res.keys())
                        raw_rows = res.fetchall()
                        formatted_rows = [
                            [str(v) if v is not None else None for v in row]
                            for row in raw_rows
                        ]
                    exec_ms = int((time.time() - exec_start) * 1000)
                    diag["execution_status"] = "success"
                    diag["execution_ms"] = exec_ms
                    diag["row_count"] = len(formatted_rows)
                    diag["execution_attempts"] = attempt + 1
                    break
                except Exception as exec_err:
                    exec_last_error = str(exec_err)
                    diag["execution_error_class"] = type(exec_err).__name__
                    if (
                        attempt >= max_exec_retries
                        or not _is_retryable_exec_error(exec_last_error)
                    ):
                        raise

                    # Runtime Self-Correction: Query passed policy checks but PostgreSQL threw a runtime error
                    # (e.g., column missing from CTE projection, type mismatch, ambiguous alias).
                    # Re-prompts the LLM with the exact Postgres error for root-cause grounded repair.
                    if attempt == 0:
                        logger.warning(
                            "[%s] SQL execution failed (%s). Attempting LLM execution self-correction.",
                            request_id, type(exec_err).__name__
                        )
                    repair_plan = SQLGenerator.regenerate_with_execution_error(
                        question=question,
                        table_name=table_name,
                        columns=col_list,
                        failed_sql=current_sql,
                        db_error=exec_last_error,
                        conversation_context=conv_context,
                        request_id=request_id,
                        schema_override=multi_table_schema
                    )
                    rep_sql = repair_plan.get("sql")
                    rep_status = repair_plan.get("status")
                    diag["execution_repair_status"] = rep_status
                    if not rep_status or not rep_sql:
                        # Unfixable / repair failed: surface original exception
                        raise exec_err

                    # Security Enforcement: Repaired SQL must pass AST validation again before running!
                    current_sql = SQLPolicyValidator.validate_and_format(rep_sql, allowed_tables)
                    diag["execution_repair_attempted"] = True
                    diag["repaired_sql"] = current_sql
                    rep_diag = repair_plan.get("diagnostics") or {}
                    diag["repair_llm_model"] = rep_diag.get("llm_model")
            exec_ms = int((time.time() - exec_start) * 1000)
            validated_sql = current_sql
        except Exception as e:
            exec_ms = int((time.time() - exec_start) * 1000)
            err_msg = str(e)
            diag["execution_status"] = "failed"
            diag["execution_ms"] = exec_ms
            if diag.get("execution_repair_attempted"):
                diag["execution_attempts"] = 2
                diag["error_category"] = "EXECUTION_ERROR_AFTER_REPAIR"
            diag["final_status"] = "failed"
            log_diagnostic_event(diag)
            ans = f"Query execution failed: {err_msg}"
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            q_run = QueryRun(
                conversation_id=conversation_id,
                question=question,
                generated_sql=current_sql,
                status="failed",
                execution_ms=exec_ms,
                error_category=diag.get("error_category", "EXECUTION_ERROR")
            )
            db.add(asst_msg)
            db.add(q_run)
            db.commit()
            return {
                "message_id": asst_msg.id,
                "answer": ans,
                "sql": validated_sql,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "visualization": None,
                "status": "failed",
                "clarification_question": None,
                "warnings": [err_msg]
            }

        # 11. Format natural language answer grounded in database execution results
        row_cnt = len(formatted_rows)
        visualization = None

        if row_cnt == 0:
            summary = f"{explanation} The query completed successfully, but returned 0 rows."

        elif row_cnt == 1 and len(cols) == 1:
            col_name = cols[0].lower()
            fmt = _format_cell_value(formatted_rows[0][0], cols[0])
            if any(k in col_name for k in ["count", "total", "rows", "matching"]):
                summary = f"There are **{fmt}** records matching your query."
            else:
                summary = f"{explanation} Result: **{fmt}**."

        elif any(k in c.lower() for c in cols for k in ["rate", "pct", "percent"]):
            rate_col = next(c for c in cols if any(k in c.lower() for k in ["rate", "pct", "percent"]))
            rate_idx = cols.index(rate_col)
            tot_idx = next((i for i, c in enumerate(cols) if any(k in c.lower() for k in ["total", "count", "rows"]) and i != rate_idx), None)
            cnt_idx = next((i for i, c in enumerate(cols) if any(k in c.lower() for k in ["count", "events", "positives", "matching"]) and i not in (0, rate_idx, tot_idx)), None)
            metric_idx = next((i for i, c in enumerate(cols) if i not in (0, rate_idx, tot_idx, cnt_idx) and any(k in c.lower() for k in ["avg", "income", "salary", "amount", "cost"])), None)

            lines = [f"{explanation}"]
            for r in formatted_rows:
                base = f"- **{r[0]}**: {r[rate_idx]}%"
                if tot_idx is not None and cnt_idx is not None:
                    base += f" ({r[cnt_idx]} out of {r[tot_idx]})"
                if metric_idx is not None:
                    base += f" with {cols[metric_idx].replace('_', ' ')}: {_format_cell_value(r[metric_idx], cols[metric_idx])}"
                lines.append(base)
            summary = "\n".join(lines)
            visualization = {"type": "bar", "x": cols[0], "y": rate_col}

        elif row_cnt <= 5 and len(cols) == 2:
            items = [f"{r[0]}: {_format_cell_value(r[1], cols[1])}" for r in formatted_rows]
            summary = f"{explanation} Found: {', '.join(items)}."
            try:
                float(formatted_rows[0][1])
                visualization = {
                    "type": "bar",
                    "x": cols[0],
                    "y": cols[1]
                }
            except (ValueError, TypeError):
                pass

        elif row_cnt <= 20 and len(cols) <= 4:
            items = [f"{r[0]} ({r[1]})" if len(r) > 1 else str(r[0]) for r in formatted_rows[:5]]
            summary = f"{explanation} Returned {row_cnt:,} records. Top entries: {', '.join(items)}."
            if len(cols) == 2 and len(formatted_rows[0]) > 1:
                try:
                    float(formatted_rows[0][1])
                    visualization = {"type": "bar", "x": cols[0], "y": cols[1]}
                except (ValueError, TypeError):
                    pass
        else:
            summary = f"{explanation} Returned {row_cnt:,} record{'s' if row_cnt != 1 else ''}."

        # 11b. Multi-Section Splitting:
        #      When a user asks for separate tables or comparisons (e.g. "Highest and Lowest Calories Burnt"),
        #      the SQL query generates a UNION ALL with a 'heading' column.
        #      The orchestrator detects this column, removes it from tabular columns, and splits rows
        #      into distinct named subsections so the frontend renders separate cards/tables.
        sections = None
        if cols and len(cols) > 1:
            heading_col_idx = None
            heading_candidates = [
                "heading", "section", "table", "category", "type", "label",
                "title", "subset", "tag", "group", "result_table", "table_name",
                "category_name", "heading_name"
            ]
            
            # Check 1: Find a column whose name indicates a heading / section
            for idx, c in enumerate(cols):
                c_low = c.lower()
                if c_low in heading_candidates or any(c_low.endswith(s) for s in ["_heading", "_label", "_section", "_title"]):
                    heading_col_idx = idx
                    break
                    
            # Check 2: If no explicit column name matched, but question asks for separate tables or subsets,
            # find if there is a column with a small number of distinct non-numeric string labels
            if heading_col_idx is None and any(w in q_lower for w in ["separate table", "separate tables", "two table", "two tables", "highest and lowest", "least and highest", "max and min"]):
                for idx, c in enumerate(cols):
                    vals = [r[idx] for r in formatted_rows if r[idx] is not None]
                    distinct_vals = list(dict.fromkeys(vals))
                    if 2 <= len(distinct_vals) <= 10 and all(isinstance(v, str) and not v.replace(".", "", 1).isdigit() for v in distinct_vals):
                        heading_col_idx = idx
                        break
                        
            if heading_col_idx is not None:
                headings = [r[heading_col_idx] for r in formatted_rows if r[heading_col_idx] is not None]
                unique_headings = []
                for h in headings:
                    if h not in unique_headings:
                        unique_headings.append(h)
                if len(unique_headings) > 1:
                    sections = []
                    sub_cols = [c for i, c in enumerate(cols) if i != heading_col_idx]
                    for uh in unique_headings:
                        sub_rows = [
                            [val for i, val in enumerate(r) if i != heading_col_idx]
                            for r in formatted_rows if r[heading_col_idx] == uh
                        ]
                        sections.append({
                            "title": str(uh),
                            "columns": sub_cols,
                            "rows": sub_rows,
                            "row_count": len(sub_rows)
                        })

        # 12. Audit Trail Persistence:
        #     - Message: Appends assistant response and metadata (SQL, row count, execution_ms).
        #     - QueryRun: Independent audit record of every generated query with execution time and status.
        #     - Telemetry: Emits sanitized JSON diagnostic events (credentials redacted).
        safe_content = summary or "Executed analytical query."
        asst_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=safe_content,
            metadata_json={
                "sql": validated_sql,
                "dataset_id": str(active_dataset.id),
                "table_name": table_name,
                "row_count": row_cnt,
                "execution_ms": exec_ms
            }
        )
        q_run = QueryRun(
            conversation_id=conversation_id,
            question=question,
            generated_sql=validated_sql,
            status="success",
            execution_ms=exec_ms
        )
        db.add(asst_msg)
        db.add(q_run)
        db.commit()

        diag["final_status"] = "success"
        log_diagnostic_event(diag)

        return {
            "message_id": asst_msg.id,
            "answer": safe_content,
            "sql": validated_sql,
            "columns": cols,
            "rows": formatted_rows,
            "row_count": row_cnt,
            "sections": sections,
            "visualization": visualization,
            "status": "success",
            "clarification_question": None,
            "warnings": []
        }
