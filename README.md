# itsm-bridge

A natural-language mediation layer over a ServiceNow-shaped IT data model. Built as a take-home for Atomicwork.

> **Why this exists.** ServiceNow stores IT data with cryptic identifiers (`sys_id`), integer-coded statuses (`state=2` means "In Progress"), reference fields pointing across tables, and a domain vocabulary alien to end users. A thin chatbot wrapper that hands raw JSON to an LLM hallucinates values and breaks on multi-hop joins. This layer translates between an LLM agent and the ITSM data model — schema graph + plan-then-execute + grounded responses — without ever giving the LLM a free hand.

The full design rationale is in [`DESIGN.md`](DESIGN.md). The build spec is in `00-overview.md` through `13-tasks.md` (also at the repo root).

## Stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, NetworkX, FAISS, sentence-transformers, structlog, official `mcp` SDK.
- **Frontend:** Next.js 14 (App Router), TypeScript, Tailwind, vis-network for the graph view.
- **LLMs:** Anthropic Claude (default) and OpenAI (alternate), behind a `BaseLLMClient` abstraction.
- **Deploy:** Vercel (frontend) + Fly.io (backend, single container running FastAPI on 8000 and MCP SSE on 8001).

## Quick start (local, Docker)

```bash
# 1) Set your LLM keys
cp .env.example .env
# Edit .env to fill in ANTHROPIC_API_KEY and/or OPENAI_API_KEY.

# 2) Bring everything up
docker compose up --build

# 3) Open the app
open http://localhost:3000
```

You should see the chat interface with four starter prompts. The Plan Inspector on the right shows the LLM's plan and the executor's trace for every query. Click "Schema graph" to see the live graph; after a query, the relations the executor traversed are highlighted in red.

The MCP SSE server is at `http://localhost:8001/sse`. Inspect with `mcp inspect http://localhost:8001/sse` (requires the MCP CLI).

## Quick start (local, no Docker)

```bash
# Backend
cd backend
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"   # Windows
# uv pip install --python .venv/bin/python -e ".[dev]"          # macOS/Linux
.venv/Scripts/python.exe -m uvicorn src.api.main:app --reload   # Windows
# .venv/bin/python -m uvicorn src.api.main:app --reload         # macOS/Linux

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

The first run downloads the sentence-transformers `all-MiniLM-L6-v2` model (~80MB, one-time, ~30s). Cached at `~/.cache/huggingface/`.

## Demo

A 5-minute walkthrough with concrete queries is in [`demo-script.md`](demo-script.md).

## Tests

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests -m "not real_llm"   # Windows
# .venv/bin/python -m pytest tests -m "not real_llm"          # macOS/Linux
```

Currently **262 passing** unit + integration tests covering schema graph, data store, KB retriever, LLM clients, plan validator, planner agents (mocked), executor, response generator, all five guardrails (incl. audit log hash chain across 100 entries), full API pipeline (every query category), write path edge cases (idempotency, expiry, lock conflict), and MCP tools.

## Eval

```bash
cd backend
.venv/Scripts/python.exe -m eval.runner eval/queries.yaml eval/reports/
```

44 gold queries from `10-eval.md`: 8 lookup, 6 KB, 6 analytical, 6 cross-reference, 3 write, 3 conversational, 4 edge case, 5 prompt injection, 3 out-of-scope. The runner produces a dated markdown report (`eval-YYYYMMDD-HHMMSS.md`) and `latest.json`. Smoke subset (3 queries) runs in CI on every push; full run costs ~$0.50 in API spend.

## Project layout

```
itsm-bridge/
├── DESIGN.md                          # The deliverable: 1500-2500 words of architecture rationale
├── README.md                          # This file
├── demo-script.md                     # 5-minute walkthrough
├── Dockerfile                         # Multi-stage; FastAPI + MCP SSE in one container
├── docker-compose.yml                 # Backend + frontend
├── fly.toml                           # Fly.io deploy config
├── .env.example                       # Every env var documented
├── 00-overview.md … 13-tasks.md       # The original spec
├── tasks/
│   ├── todo.md                        # Phase tracker (every phase ✓ with review)
│   └── lessons.md                     # Self-improvement log
├── backend/
│   ├── pyproject.toml                 # Python 3.12; uv + ruff + mypy + pytest
│   ├── src/
│   │   ├── core/                      # schema_graph, schema_loader, data_store, in_memory_store
│   │   ├── planner/                   # plan_schema, validator, preprocessor, plan_generator,
│   │   │   ├── prompts/               #   name_resolver, few_shot, prompts/{*.md, few_shot.yaml}
│   │   ├── executor/                  # engine, operations (6 handlers), reference_resolver, trace
│   │   ├── knowledge/                 # embeddings, indexer, retriever (FAISS)
│   │   ├── response/                  # generator (quarantined LLM), grounding
│   │   ├── guardrails/                # 5 named defenses
│   │   ├── write_path/                # proposal, approval_store, executor (HITL)
│   │   ├── llm/                       # base, anthropic, openai, factory, retry
│   │   ├── api/                       # main, deps, middleware, session, rate_limit, types, routes/
│   │   └── mcp_server/                # server, tools (6 MCP tools)
│   ├── data/                          # schema.yaml + 4 JSON fixtures (10 incidents, 10 users, 4 groups, 5 KB)
│   ├── eval/                          # queries.yaml (44 gold queries), runner, reporter, reports/
│   └── tests/                         # unit/, integration/, conftest.py (with ScriptedLLM)
└── frontend/
    ├── app/                           # page.tsx (chat) + schema/page.tsx (vis-network graph)
    ├── components/                    # ChatPanel, PlanInspector, MessageInput, ApprovalDialog
    └── lib/                           # api.ts, types.ts (TypeScript mirror of backend Pydantic)
```

## Deployment

`scripts/deploy.sh backend` deploys the backend to Fly.io. `scripts/deploy.sh frontend` deploys the frontend to Vercel. One-time setup:

```bash
fly auth login
fly apps create itsm-bridge-backend
fly volumes create itsm_data --size 1
fly secrets set ANTHROPIC_API_KEY=... OPENAI_API_KEY=...

cd frontend && vercel login && vercel link
```

CI deploys backend on push to `main` via `.github/workflows/deploy.yml` (needs `FLY_API_TOKEN` secret). Vercel deploys the frontend via its native GitHub integration.

## What's NOT implemented (named in DESIGN.md)

- Real ServiceNow REST integration (the `DataStore` Protocol is the swap point).
- Full RBAC / multi-tenant auth.
- Persistent vector DB (FAISS in-memory only).
- True token-by-token LLM streaming (`/v1/query/stream` SSE is spec'd but not built — frontend uses POST).
- OpenTelemetry / Sentry / production-grade observability.
- MCP package directory is `src/mcp_server/` (not `src/mcp/`) to avoid shadowing the `mcp` PyPI package — small spec deviation noted in `DESIGN.md`.

## License

Take-home project — all rights reserved by the author for assignment review purposes.
