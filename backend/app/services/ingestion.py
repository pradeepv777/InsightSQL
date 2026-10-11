import os
import uuid
import re
import json
from io import BytesIO
from typing import Dict, Any, List, Tuple
import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import engine
from app.models.models import Dataset, DatasetTable, ColumnMetadata, RetrievalDocument

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet", ".json"}

def sanitize_identifier(name: str) -> str:
    """Sanitizes an identifier to a safe PostgreSQL lowercase column name."""
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', name.strip().lower())
    clean = re.sub(r'_+', '_', clean).strip('_')
    if not clean or clean[0].isdigit():
        clean = f"col_{clean}"
    return clean[:63]

def generate_safe_table_name() -> str:
    """Generates a safe internal table name that does not collide and cannot be injected."""
    uid = uuid.uuid4().hex[:12]
    return f"ds_tbl_{uid}"

def map_dtype_to_postgres(series: pd.Series) -> str:
    """Conservatively maps pandas dtypes to standard PostgreSQL data types."""
    dtype_str = str(series.dtype).lower()
    
    if "int64" in dtype_str:
        return "BIGINT"
    elif "int32" in dtype_str or "int16" in dtype_str or "int8" in dtype_str:
        return "INTEGER"
    elif "float" in dtype_str:
        return "DOUBLE PRECISION"
    elif "bool" in dtype_str:
        return "BOOLEAN"
    elif "datetime" in dtype_str:
        return "TIMESTAMP"
    else:
        # Check if sample strings look like dates or numbers
        non_nulls = series.dropna().head(50)
        if len(non_nulls) > 0 and pd.to_datetime(non_nulls, errors='coerce').notnull().all():
            return "TIMESTAMP"
        return "TEXT"

class IngestionService:
    @classmethod
    def parse_file(cls, filename: str, content: bytes) -> pd.DataFrame:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file format '{ext}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
            
        if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError(f"File size exceeds limit of {settings.MAX_UPLOAD_MB}MB")

        bio = BytesIO(content)
        
        try:
            if ext == ".csv":
                # Try common encodings
                try:
                    df = pd.read_csv(bio, nrows=settings.MAX_IMPORT_ROWS)
                except UnicodeDecodeError:
                    bio.seek(0)
                    df = pd.read_csv(bio, encoding="latin1", nrows=settings.MAX_IMPORT_ROWS)
            elif ext in [".xlsx", ".xls"]:
                df = pd.read_excel(bio, nrows=settings.MAX_IMPORT_ROWS)
            elif ext == ".parquet":
                df = pd.read_parquet(bio)
                if len(df) > settings.MAX_IMPORT_ROWS:
                    df = df.iloc[:settings.MAX_IMPORT_ROWS]
            elif ext == ".json":
                bio.seek(0)
                try:
                    df = pd.read_json(bio)
                except ValueError:
                    bio.seek(0)
                    data = json.load(bio)
                    if isinstance(data, list):
                        df = pd.json_normalize(data)
                    else:
                        df = pd.json_normalize([data])
                if len(df) > settings.MAX_IMPORT_ROWS:
                    df = df.iloc[:settings.MAX_IMPORT_ROWS]
            else:
                raise ValueError(f"Unsupported format: {ext}")
        except Exception as e:
            raise ValueError(f"Failed to parse {filename}: {str(e)}")

        if df.empty:
            raise ValueError("The uploaded dataset contains no rows")

        # Normalize column names and handle duplicates
        raw_cols = [str(c) if c is not None else "col" for c in df.columns]
        normalized_cols = []
        counts: Dict[str, int] = {}
        for c in raw_cols:
            clean = sanitize_identifier(c)
            if clean in counts:
                counts[clean] += 1
                clean = f"{clean}_{counts[clean]}"
            else:
                counts[clean] = 0
            normalized_cols.append(clean)
            
        df.columns = normalized_cols
        return df

    @classmethod
    def ingest_dataset(
        cls,
        db: Session,
        filename: str,
        content: bytes,
        owner_id: str = "default_user",
        display_name: str = None
    ) -> Dataset:
        ext = os.path.splitext(filename)[1].lower()
        disp_name = display_name or os.path.splitext(filename)[0]
        
        # 1. Parse file safely
        df = cls.parse_file(filename, content)
        row_count = len(df)
        
        # 2. Record initial dataset entry
        dataset = Dataset(
            owner_id=owner_id,
            display_name=disp_name,
            source_filename=filename,
            source_format=ext.lstrip('.'),
            status="processing",
            row_count=row_count
        )
        db.add(dataset)
        db.commit()
        db.refresh(dataset)
        
        table_name = generate_safe_table_name()
        
        try:
            # 3. Infer types
            col_types = {}
            for col in df.columns:
                col_types[col] = map_dtype_to_postgres(df[col])
                
            # 4. Create internal PostgreSQL table
            col_defs = [f'"{col}" {col_types[col]}' for col in df.columns]
            create_sql = f'CREATE TABLE "{table_name}" ({", ".join(col_defs)});'
            
            with engine.begin() as conn:
                conn.execute(text(create_sql))
                
            # 5. Load records to PostgreSQL table
            # Convert timestamps/objects safely before inserting
            clean_df = df.copy()
            for col, pg_type in col_types.items():
                if pg_type == "TIMESTAMP":
                    clean_df[col] = pd.to_datetime(clean_df[col], errors='coerce')
                elif pg_type in ("INTEGER", "BIGINT"):
                    clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce')
                elif pg_type == "DOUBLE PRECISION":
                    clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce')

            clean_df.to_sql(
                table_name,
                con=engine,
                if_exists="append",
                index=False,
                chunksize=5000
            )

            # 6. Save DatasetTable metadata
            ds_table = DatasetTable(
                dataset_id=dataset.id,
                schema_name="public",
                table_name=table_name,
                display_name=disp_name,
                row_count=row_count
            )
            db.add(ds_table)
            db.commit()
            db.refresh(ds_table)

            # 7. Compute column metrics & profile
            for col in df.columns:
                null_count = int(df[col].isnull().sum())
                unique_count = int(df[col].nunique())
                sample_vals = [str(v) for v in df[col].dropna().unique()[:5]]
                
                profile = {
                    "null_count": null_count,
                    "unique_count": unique_count,
                    "sample_values": sample_vals
                }
                
                col_meta = ColumnMetadata(
                    dataset_table_id=ds_table.id,
                    column_name=col,
                    database_type=col_types[col],
                    description=f"Column '{col}' storing {col_types[col].lower()} values.",
                    nullable=null_count > 0,
                    profile_json=profile
                )
                db.add(col_meta)

            # 8. Create Retrieval Documentation documents for RAG
            schema_summary = f"Table '{table_name}' (named '{disp_name}') has {row_count} rows and columns: " + \
                ", ".join([f"{c} ({col_types[c]})" for c in df.columns])
                
            doc_table = RetrievalDocument(
                dataset_id=dataset.id,
                document_type="table_schema",
                content=schema_summary,
                metadata_json={
                    "table_name": table_name,
                    "columns": list(df.columns),
                    "row_count": row_count
                }
            )
            db.add(doc_table)

            for col in df.columns:
                doc_col = RetrievalDocument(
                    dataset_id=dataset.id,
                    document_type="column_description",
                    content=f"Column '{col}' in table '{table_name}' ({disp_name}). Type: {col_types[col]}. Sample values: {df[col].dropna().unique()[:3].tolist()}",
                    metadata_json={
                        "table_name": table_name,
                        "column_name": col,
                        "type": col_types[col]
                    }
                )
                db.add(doc_col)

            dataset.status = "ready"
            db.commit()
            db.refresh(dataset)
            return dataset

        except Exception as e:
            db.rollback()
            dataset.status = "failed"
            dataset.error_summary = str(e)
            db.commit()
            # Clean up created table if it failed
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
            except Exception:
                pass
            raise e
