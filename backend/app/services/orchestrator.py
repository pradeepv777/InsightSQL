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

class QueryOrchestrator:
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

        # Initialize diagnostic tracker for Step 3 safe diagnostic logging
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
        
        # 1. Ensure conversation exists
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conv:
            conv = Conversation(id=conversation_id, title="Data Analysis")
            db.add(conv)
            db.commit()

        # 2. Record User Message
        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=question
        )
        db.add(user_msg)
        db.commit()

        # 3. Resolve active dataset
        active_dataset = None
        if dataset_ids and len(dataset_ids) > 0:
            active_dataset = db.query(Dataset).filter(Dataset.id.in_(dataset_ids), Dataset.status == "ready").first()

        # Check if conversation already has an active dataset from previous turns
        if not active_dataset:
            last_msg_with_ds = db.query(Message).filter(
                Message.conversation_id == conversation_id,
                Message.metadata_json.isnot(None)
            ).order_by(Message.created_at.desc()).first()
            if last_msg_with_ds and isinstance(last_msg_with_ds.metadata_json, dict):
                prev_ds_id = last_msg_with_ds.metadata_json.get("dataset_id")
                if prev_ds_id:
                    active_dataset = db.query(Dataset).filter(Dataset.id == prev_ds_id, Dataset.status == "ready").first()

        # If still no dataset, check recent QueryRuns for this conversation
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

        # If no explicit or historical dataset was selected, find the ready dataset whose schema columns best match the question
        if not active_dataset:
            import re
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

        if not active_dataset:
            # Pick the most recently imported ready dataset
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

        # Find primary table for active dataset
        ds_table = db.query(DatasetTable).filter(DatasetTable.dataset_id == active_dataset.id).first()
        if not ds_table:
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

        table_name = ds_table.table_name
        diag["table_name"] = table_name
        columns_meta = db.query(ColumnMetadata).filter(ColumnMetadata.dataset_table_id == ds_table.id).all()
        col_list = [{"name": c.column_name, "type": c.database_type} for c in columns_meta]

        # 4. Check for direct metadata profile questions
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

        # 5. Check for documentation inquiry using RAG
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

        # 6. Retrieve relevant RAG context
        retrieved_docs = RAGService.retrieve_context(db, question, [active_dataset.id], top_k=4)
        diag["retrieved_docs"] = [
            {"id": str(d.get("id")), "score": d.get("score"), "type": d.get("type")}
            for d in retrieved_docs
        ]
        rag_context = [d["content"] for d in retrieved_docs]

        # 7. Load recent conversation history (excluding current message)
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

        # 8. Generate SQL
        sql_plan = SQLGenerator.generate_sql(
            question=question,
            table_name=table_name,
            columns=col_list,
            retrieved_context=rag_context,
            conversation_context=conv_context,
            request_id=request_id
        )

        plan_diag = sql_plan.get("diagnostics", {})
        diag["prompt_summary"] = plan_diag.get("prompt_summary")
        diag["llm_provider"] = plan_diag.get("llm_provider")
        diag["llm_model"] = plan_diag.get("llm_model")
        diag["llm_fresh_response"] = plan_diag.get("fresh_response", False)
        diag["generation_mode"] = plan_diag.get("mode", "unknown")
        diag["proposed_sql"] = sql_plan.get("sql")

        # Handle clarification needed
        if sql_plan.get("status") == "clarification_needed":
            ans = sql_plan.get("explanation") or sql_plan.get("clarification_question") or "Clarification is needed before running this query."
            clarification = sql_plan.get("clarification_question") or ans
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
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

        # 9. Validate SQL against strict security policy
        allowed_tables = {table_name}
        validated_sql = None
        try:
            validated_sql = SQLPolicyValidator.validate_and_format(proposed_sql, allowed_tables)
            diag["sql_validation"] = "passed"
        except SQLPolicyError as spe:
            # If LLM proposed invalid SQL or an unauthorized table in a follow-up, try dynamic analytical generator
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

        # 10. Execute query safely with read-only connection & statement timeout
        exec_start = time.time()
        diag["queried_postgres"] = True
        try:
            with query_engine.connect() as conn:
                res = conn.execute(text(validated_sql))
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
        except Exception as e:
            exec_ms = int((time.time() - exec_start) * 1000)
            err_msg = str(e)
            diag["execution_status"] = "failed"
            diag["execution_ms"] = exec_ms
            diag["final_status"] = "failed"
            log_diagnostic_event(diag)
            ans = f"Query execution failed: {err_msg}"
            asst_msg = Message(conversation_id=conversation_id, role="assistant", content=ans)
            q_run = QueryRun(
                conversation_id=conversation_id,
                question=question,
                generated_sql=validated_sql,
                status="failed",
                execution_ms=exec_ms,
                error_category="EXECUTION_ERROR"
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
            val = formatted_rows[0][0]
            col_name = cols[0].lower()
            
            # Format count / employee totals
            if "employee" in col_name or "count" in col_name or "rows" in col_name or "occurrences" in col_name:
                try:
                    num_val = int(val)
                    if "left" in col_name or "attrition" in q_lower:
                        summary = f"**{num_val:,}** employees left the company (attrition = 'Yes')."
                    elif "employee" in col_name or "total" in col_name:
                        summary = f"There are **{num_val:,}** employees in this dataset."
                    else:
                        summary = f"{explanation} Result: **{num_val:,}**."
                except (ValueError, TypeError):
                    summary = f"{explanation} Result: **{val}**."
            # Format monetary amounts or averages
            elif any(k in col_name for k in ["income", "salary", "amount", "price", "revenue", "avg", "cost"]):
                try:
                    num_val = float(val)
                    summary = f"{explanation} Result: **${num_val:,.2f}**."
                except (ValueError, TypeError):
                    summary = f"{explanation} Result: **{val}**."
            else:
                summary = f"{explanation} Result: **{val}** ({cols[0]})."

        elif any("attrition_rate" in c for c in cols):
            # Specific rate comparison breakdown across groups (column name agnostic)
            rate_col_name = next(c for c in cols if "attrition_rate" in c)
            rate_idx = cols.index(rate_col_name)
            tot_idx = cols.index("total_employees") if "total_employees" in cols else None
            cnt_idx = cols.index("attrition_count") if "attrition_count" in cols else None
            group_name = cols[0].replace("_", " ").title()

            lines = [f"{explanation}"]
            for r in formatted_rows:
                grp = r[0]
                pct = r[rate_idx]
                if tot_idx is not None and cnt_idx is not None:
                    tot = r[tot_idx]
                    cnt = r[cnt_idx]
                    lines.append(f"- **{grp}**: {pct}% ({cnt} left out of {tot} employees)")
                else:
                    lines.append(f"- **{grp}**: {pct}%")
            summary = "\n".join(lines)

            visualization = {
                "type": "bar",
                "x": cols[0],
                "y": rate_col_name
            }

        elif row_cnt <= 5 and len(cols) == 2:
            items = [f"{r[0]}: {r[1]}" for r in formatted_rows]
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
            items = [f"{r[0]} ({r[1]})" for r in formatted_rows[:5]]
            summary = f"{explanation} Returned {row_cnt:,} records. Top entries: {', '.join(items)}."
            if len(cols) == 2:
                try:
                    float(formatted_rows[0][1])
                    visualization = {"type": "bar", "x": cols[0], "y": cols[1]}
                except (ValueError, TypeError):
                    pass
        else:
            summary = f"{explanation} Returned {row_cnt:,} record{'s' if row_cnt != 1 else ''}."

        # Check for multiple named result sections (e.g. if query has a heading column from UNION or categories)
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

        # 12. Save Assistant Message and Query Run
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
