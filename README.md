# InsightSQL: Natural Language Data Explorer

A minimal, secure natural-language data analysis system that imports user datasets into PostgreSQL, translates analytical questions into verified SQL queries using RAG context, and provides transparent answers backed by database execution results.

---

## Architecture & Visual Design

- **UI Match**: Exactly matches the requested minimal dark viewport with rounded borders, message bubbles, pill-shaped input, embedded send button, and adjacent `+` upload button.
- **Frontend**: React 19 + TypeScript + Vite + Tailwind CSS v4.
- **Backend**: FastAPI + SQLAlchemy 2.0 + Pydantic v2 + sqlglot + pandas + pyarrow.
- **Database & Retrieval**: PostgreSQL with pgvector-compatible schema, metadata document store, and lexical/semantic RAG.
- **Security**: PostgreSQL AST validation via `sqlglot`, strict single `SELECT` enforcement, dataset table allowlisting, system schema blocking, function blacklisting, statement timeouts, and row limits.

---

## Verification of UI Component `@skiper-ui/skiper83`

Per project instructions, `@skiper-ui/skiper83` was inspected and verified against the official package registry:
- Querying `npm info @skiper-ui/skiper83` returned `E404 Not Found`.
- To fulfill the exact layout, proportions, restrained borders, and colors shown in the reference screenshot, the chat interface was built from first principles using Tailwind CSS v4, Lucide icons, and modern React without bulky third-party dashboard overhead.

---

## Quickstart

### Prerequisites
- Python 3.12+ (or 3.13)
- Node.js 18+ & npm
- PostgreSQL (local or Docker)

### 1. Configure Environment
Copy `.env.example` or create `.env`:
```bash
cp Notes/.env.example .env
```
Ensure `DATABASE_URL` matches your local database:
```env
DATABASE_URL=postgresql+psycopg://postgres:root@localhost:5432/ai_analyst
LLM_API_KEY=your_openai_api_key_here  # Optional: app includes built-in analytical engine fallback
LLM_MODEL=gpt-4o-mini
```

### 2. Backend Setup
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```
API Documentation is available at `http://localhost:8000/docs`.

### 3. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## Testing & Evaluation Suite

Run the full automated test suite (including ingestion, security policy, evaluation benchmark, and API integration):

```bash
cd backend
python -m pytest tests/ -v
```

### Verified Test Cases (12/12 from `test_cases.csv`):
1. **Aggregation**: Exact SQL average calculation (`AVG`).
2. **Filter**: Case-insensitive filtering (`WHERE LOWER(city) = 'hyderabad'`).
3. **Ranking**: Group-by and top-K ordering (`ORDER BY total DESC LIMIT 5`).
4. **Duplicates**: Duplicate occurrence detection (`HAVING COUNT(*) > 1`).
5. **Nulls**: Missing-value profile counts per column.
6. **Metadata**: Direct row and column count inspection.
7. **Follow-up**: Conversational memory context resolution ("Now only show Hyderabad").
8. **Ambiguity**: Proper clarification requests for undefined metric boundaries.
9. **Security**: Detection and rejection of destructive DDL/DML (`DROP TABLE`, `DELETE`, etc.).
10. **Isolation**: Rejection of unauthorized table access.
11. **Timeout**: Bounded query execution limits.
12. **Documentation**: RAG retrieval of business terms and column definitions.
