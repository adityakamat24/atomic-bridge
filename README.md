# Atomic Bridge

A natural-language layer over a ServiceNow-shaped IT data model. Built as the Atomicwork take-home.

## The fastest way to evaluate this

**The system is hosted. You don't need to clone, install, or run anything.** Open the live demo and start typing:

> **Live demo: https://atomic-bridge.vercel.app/**

- **Backend API:** https://itsm-bridge-backend.fly.dev (FastAPI, health at `/health`)
- **MCP SSE endpoint:** https://itsm-bridge-backend.fly.dev:8001/sse (six tools, ready for any MCP client)

Suggested starter queries in the demo cover all four query categories the PDF asks about. Local setup instructions are at the bottom if you want them, but the hosted demo is the recommended path. The Fly machine is pinned to always-on (`min_machines_running = 1`), so there's no cold start on the first request.

> The live demo runs in **admin view**. Every session has full read access across all tables, can see every field (including ones flagged sensitive in the schema), and can propose write changes to any incident. Per-user scoping is called out in [What I'd do differently](#what-id-do-differently).

ServiceNow stores IT data with `sys_id` references, integer-coded statuses, and field names like `assignment_group`. The user thinks in employees, tickets, severity, teams. This is the layer that translates between the two, plans the work as a structured operation list, and only then executes against the data.

## Stack

Backend is Python 3.12 with FastAPI, Pydantic v2, NetworkX, FAISS, sentence-transformers, and the official `mcp` SDK. Frontend is Next.js 14 with Tailwind and vis-network. LLMs are Anthropic Claude by default and OpenAI as an alternate, both behind one Protocol. Deploys to Vercel for the frontend and Fly.io for the backend (one container, two services: FastAPI on 8000, MCP SSE on 8001).

## Setup

Local with Docker:

```bash
cp .env.example .env   # set ANTHROPIC_API_KEY
docker compose up --build
open http://localhost:3000
```

Local without Docker:

```bash
cd backend
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"   # mac/linux: .venv/bin/python
.venv/Scripts/python.exe -m uvicorn src.api.main:app --reload

# in a second terminal
cd frontend && npm install && npm run dev
```

The first run downloads ~80MB of `all-MiniLM-L6-v2` weights into `~/.cache/huggingface`. After that it's cold-to-ready in about 2 seconds.

If you have an Anthropic key you can run the full eval (~$0.50 in credits). Without one, the smoke subset and all unit and integration tests run free.

---

# Design

The PDF asks for four things in the design write-up: how the graph schema is laid out, how the planner works, the trade-offs behind the choice of graph representation, and what I'd do differently with more time. Those are the four sections below.

## The graph

The schema lives in `backend/data/schema.yaml` and gets loaded once at startup into a `networkx.MultiDiGraph`. Five entity types: `incident`, `sys_user`, `sys_user_group`, `kb_knowledge`, and `category`. Two value maps (one each for `state` and `priority`). Thirteen relations.

There are four kinds of node. **Entities** are tables. **Fields** are columns; each carries display name, data type, an optional sensitivity flag, and an optional `references` pointer (so `incident.caller_id` points at `sys_user`). **ValueMaps** are the integer-to-label dictionaries, modeled as first-class nodes rather than buried as constants somewhere, so the LLM can see the mapping when it reads the schema. **Relations** are named, directional, semantic verbs between two entities.

A Relation looks like this in YAML:

```yaml
id: incident.reportedBy
verb_phrase: "reported by"
from_entity: incident
to_entity: sys_user
via_field: incident.caller_id
cardinality: many_to_one
inverse_relation_id: sys_user.incidentsReported
```

The planner sees `verb_phrase`. The executor walks `via_field`. Neither layer knows what the other knows. The Relation node is the bridge between them, and that separation is the central architectural claim of the prototype.

Six edge types tie everything together. `HAS_FIELD` connects an entity to its columns. `MAPS_THROUGH` connects a field to its value map. `VIA` connects a relation to the field that physically implements it. `FROM` and `TO` connect a relation to its two endpoints. `INVERSE_OF` connects a relation to its dual.

The PDF specifically calls out three relationships that "should be edges":

1. `incident.caller_id → user`. Present as `incident.reportedBy`.
2. `incident.assignment_group → group`. Present as `incident.handledBy`.
3. *"A KB article's kb_category maps to an incident's category"*. Present as a four-relation bridge through a `category` entity.

The third one is worth a paragraph. `Category` is a first-class entity with one trick: the `sys_id` of each Category record is the category name itself (`"Network"`, `"Software"`, etc.). That means an existing `incident.category="Network"` already references the right record. No data files changed, only `schema.yaml` and a derived `categories.json`. The bridge is a real graph walk: `kb → category → incidents in that category`. Test: `test_kb_to_incidents_via_category_bridge`.

### How the graph gets traversed

At planning time, the planner is given a markdown serialization of the schema produced by `SchemaGraph.to_llm_context()`. The preprocessor returns a list of `relevant_entities` based on the user's question, and the plan generator runs `expand_subgraph(seeds, max_hops=2)` over the relation graph in both directions before serializing. Without that expansion the planner only sees the entities the preprocessor directly named. If the preprocessor returned `[sys_user]` for "Ravi's team", the planner wouldn't see `sys_user_group` in its context and couldn't emit `incident.handledBy`. At our scale of 5 entities the BFS expansion is effectively a no-op, but at 50+ tables it's the only way to keep the planner's context tractable without giving up reachability.

At execution time, the engine walks Relation nodes directly. The traverse handler reads `cardinality` and decides whether to gather sys_ids and call `store.get_many`, or do the inverse path with `store.find(target_entity, [Filter on via_field in source_ids])`. The handler has zero semantic knowledge. It doesn't know that "manages" means anything. Knowing what verb to walk was the planner's job; following an edge is all the executor does.

## The planner

Three agents. Preprocessor and plan generator are each one LLM call. The validator is pure Python.

**Preprocessor.** Classifies intent into one of seven values (`lookup`, `knowledge`, `analytical`, `cross_reference`, `write_proposal`, `ambiguous`, `out_of_scope`). Extracts entity mentions. Resolves pronouns against the prior turn. Returns the relevant subgraph.

Before the LLM call, a deterministic `NameResolver` runs and pre-attaches candidate matches to the prompt (fuzzy match on user names, including a token-prefix tier so "John D." matches "John Doe"). The LLM disambiguates rather than recalls. After the LLM call, a post-processing step enriches every resolved `sys_user` mention with `department`, `location`, `direct_reports`, and `groups_managed` from the data store. The planner uses those fields to pick the right relation for ambiguous possessives like "X's tickets" or "X's team".

**Plan generator.** One LLM call via tool use. The output is a `QueryPlan`, schema-constrained by Pydantic. Six op types: `find`, `traverse`, `aggregate`, `kb_lookup`, `resolve`, `write_proposal`. Each op has an `id` that later ops reference as `$id`. Few-shot examples are retrieved by query-embedding similarity from a YAML library.

**Plan validator.** Pure Python. Verifies every entity, field, and relation in the plan exists. Verifies every `$var` reference is defined upstream. Enforces resource limits. Rejects plans where `write_proposal` isn't the last op. It also *transforms* the plan: filter values for value-mapped fields are converted from display strings to integer codes (`"In Progress"` to `2`), and `is_sensitive` fields are stripped from `resolve.fields` unless the session has `view_pii`. Rejection is hard. The API returns a 400 with the validator errors. No silent retry, because retries hide injection attempts.

A worked plan for *"Show me incidents raised by people in Engineering":*

```json
{
  "intent": "cross_reference",
  "reasoning": "Find Engineering users, traverse the incidentsReported relation, resolve.",
  "operations": [
    {"op": "find", "id": "u", "entity": "sys_user",
     "filters": [{"field": "department", "operator": "eq", "value": "Engineering"}]},
    {"op": "traverse", "id": "i", "from": "$u", "relation": "sys_user.incidentsReported"},
    {"op": "resolve", "id": "out", "source": "$i",
     "fields": ["number", "state", "priority"], "include_relations": ["reportedBy"]}
  ],
  "output_spec": {"format": "list", "final_var": "out", "max_items_shown": 20},
  "confidence": 0.92
}
```

### Why a plan, not a tool-call loop

Three reasons. A plan is **inspectable**: the frontend renders it before the answer arrives, and the user can see what the system decided to do. It's **replayable**: the same plan against the same data is deterministic, so the eval suite can match on substrings without flake. And it's **replaceable**: a fine-tuned classifier could emit the same JSON shape tomorrow with no changes downstream. None of that is true for a tool-call loop, where the model's choices are scattered across multiple turns and only legible through trace logs.

### Role-aware disambiguation

Phrases like "X's tickets" and "X's team" are ambiguous against the data. End users (John Doe in Engineering) are callers of their own incidents. IT-side users (Ravi in IT Support, Priya in IT Support, Mike in Facilities) never raise tickets, they handle them. Managers (Deepak, Alex) often have no assignments at all.

The preprocessor handles this by enriching every resolved `sys_user` mention with `department`, `location`, `direct_reports`, and `groups_managed`. The plan-generator prompt then picks the relation from those fields. "X's tickets" routes to `sys_user.incidentsReported` for end users and `sys_user.incidentsAssigned` for IT staff. "X's team" has a three-way dispatch: `groups_managed > 0` walks `sys_user.managesGroups` (Deepak's case, since he manages all five assignment groups), `direct_reports > 0` with no managed groups walks `sys_user.manages` (Alex's case, five direct reports but no group ownership), everything else walks `sys_user.incidentsAssigned` then `incident.handledBy` (the staff case). Each branch has a few-shot example.

## Trade-offs in graph representation

Three approaches were on the table.

**Neo4j with the LLM emitting Cypher.** The most authentic option. The LLM emits Cypher, Neo4j handles join planning, you get a real query language for free. I ruled it out for three reasons. First, Cypher injection is harder to gate than a structured plan. The validator I wrote is around 250 lines of Pydantic and dispatch; equivalent Cypher analysis would be a small static analyzer in its own right. Second, Neo4j adds a service to deploy and a license to track for a take-home that doesn't need either. Third, and the decisive one, I wanted the schema to live in the application process so it could be loaded from YAML, traversed deterministically by Python, and tested by reading objects rather than running queries. Neo4j would push schema knowledge into a database, which is the wrong locus of control for a prototype meant to make the schema *easy to reason about*.

**A plain adjacency dict.** What I started with. Looked like `{entity: {relation_name: target_entity}}`. Worked for one-hop. Broke when I needed to ask, "what's the cardinality of this relation in the reverse direction?", the kind of question that needs relation metadata, not just topology. I could have added a parallel `relations_meta` dict and a `field_meta` dict and a `value_maps` dict, but at some point you've reinvented a graph library and done it badly.

**NetworkX `MultiDiGraph`.** Where I landed. It gives directed edges with typed attributes, parallel edges between the same node pair (useful for the `INVERSE_OF` edges that share endpoints with the relations they invert), and a BFS primitive used for `shortest_relation_path` and `expand_subgraph`. It's in-process: no service to deploy, no migrations, nothing to operate. The cost is that the graph isn't queryable in a structured language. You can't hand a reviewer a Cypher query and say "this is what the planner does." Instead the (filtered) graph is serialized to a markdown blob via `to_llm_context()` and passed to the LLM. That serialization step is the main thing I'd revisit if the graph needed to scale past 50 tables. At that point the planner's context window starts to matter and a retrieval-based subgraph selection would sit on top of the structural BFS expansion.

A practical consequence of choosing in-process NetworkX is that the whole stack is one container. The backend cold-starts in about 2 seconds. The schema is a YAML file. Adding a new ITSM table is a YAML edit, demonstrated mechanically by `test_adding_entity_to_yaml_is_reflected_without_code_changes`. That test parses the production schema, appends a new `change_request` entity, reloads, and asserts it appears in the planner's prompt. No Python changed.

## What I'd do differently

A few items, ranked by what would move the needle most.

**Stronger grounding on the response side.** The response generator is well-defended by the Dual LLM separation (planner can't see data, response generator has no tools) and by spotlighting and sandwich prompting. The grounding check itself is regex-based: it scans the response for invented INC and KB numbers and sys_ids that aren't in the executor's outputs. That catches the obvious cases but would miss a hallucinated user name like "John Smith" if the actual data only had "John Doe." The right pattern is extracting entity references from the response and verifying each one against the executor's outputs.

**Category as a hierarchy, not a flat list.** Right now `Category` has no parent/child relations. In a real ServiceNow instance categories tree (Cloud Services → AWS → S3). Adding that is a single self-referential relation on the entity, but doing it well means rethinking how the KB retriever does category matching when the user mentions a parent and you want children too.

**Table inheritance.** ServiceNow uses single-table inheritance heavily (`task` is the parent of `incident`, `problem`, `change_request`). My schema is flat. A real production extension would need an `INHERITS_FROM` edge type and an executor that understands a query on `task` should fan out across children. Out of scope for a prototype, but it's the schema change that matters most when scaling beyond a single table type.

**Date-range filters.** No current-time injection. "Incidents this week" doesn't translate. The planner has `gt` and `lt` operators on datetime fields but no notion of `now`. Small change to add, valuable for any reporting use case.

**SSE streaming on `/v1/query`.** Right now the frontend submits a POST and waits for the full response (plan plus trace plus answer) before rendering anything. Streaming would let the plan render as soon as it was generated, then trace steps as the executor walked them, then the answer. The change that would visibly improve perceived latency the most.

**Per-user views and group-scoped data.** The current build runs in admin view, so the demo shows everything to everyone. With more time I'd add real user groups and route information to the right set of people. An end user would only see their own tickets and the KB. An IT agent would see their queue plus the queue of any group they belong to. A team manager would see their groups' workload but not other teams'. A director would see across the org. The plumbing for this already half-exists: sessions carry a `view_pii` capability that the plan validator uses to strip sensitive fields, and the schema flags fields with `is_sensitive`. What's missing is the identity layer (a real auth scheme that ties a session to a `sys_user` and a set of group memberships) and a row-level filter pass on top of every `find` and `traverse` so the executor enforces visibility before resolving.

---

# Resilience and engineering details

A handful of small things that aren't load-bearing for the four required components but show how I think about production. Most of these are dull on their own and useful when something goes wrong at 3am.

**Cross-provider LLM fallback.** Every LLM client is wrapped in a `FallbackLLMClient` when both API keys are set. The primary provider (Anthropic by default) runs first. On a terminal failure after its retry loop is exhausted (the OpenAI API has a 12-minute outage, Anthropic is silently dropping requests, etc.), the secondary provider runs the same call once. `LLMToolCallMissingError` is explicitly *not* failed over because it signals a planner-prompt bug rather than a provider issue, and switching models would mask the real cause. Toggle with `LLM_ENABLE_FALLBACK`. See `backend/src/llm/fallback.py` and the four tests in `test_llm.py` under "Fallback wrapping".

**Retry with exponential backoff and jitter.** `LLMRateLimitError` and `LLMTransientError` (which wraps `APITimeoutError`, `APIConnectionError`, and `InternalServerError` from both SDKs) are retried up to 3 times with delays of 1, 2, and 4 seconds plus 25% jitter. Capped at 16s per attempt. Other LLM errors propagate immediately so the request fails fast rather than hanging the client. See `backend/src/llm/retry.py`.

**Token-bucket rate limiting.** 60 requests per minute per client IP, configurable via `RATE_LIMIT_PER_MIN`. Returns HTTP 429 with a clear reason. See `backend/src/api/rate_limit.py`.

**Session TTL.** Conversation context expires after 30 minutes of inactivity (`SESSION_TTL_SECONDS=1800`). Sessions are an in-memory ring buffer of recent turns; the preprocessor consumes the prior turn to resolve pronouns.

**Write-proposal expiry and optimistic locking.** Proposals expire after 10 minutes (`PROPOSAL_TTL_SECONDS=600`). On confirm, the handler re-fetches the target and checks `sys_updated_on` against the snapshot taken at propose time. If another write landed in between, the confirm fails with a clean conflict and the user re-issues the proposal. No silent overwrites.

**Hash-chained audit log.** Every request is appended to an NDJSON log with `sha256(prev_hash + entry)` chaining. Tampering anywhere in the chain breaks every subsequent hash. Verified across 100 sequential entries by `test_guardrails.py`.

**Input validation, fail-fast.** Length cap (5000 chars), Unicode NFKC normalization, banned-substring scan, and an advisory injection-pattern detector (scores but doesn't block, since the Dual LLM separation is the real defense). Bad input returns HTTP 400 before any LLM is touched.

**Lazy ML loading.** The sentence-transformer model and FAISS KB index are not loaded in the request path. The model loads on first `embed()` call and the index builds on first `search()`. A FastAPI lifespan background task preloads both 5 to 15 seconds after boot, so the first real request never pays the warmup cost. The `/health` endpoint returns in about 2 seconds while warmup is still in flight.

**Reference-resolver cache.** `ReferenceResolver` memoizes `(entity, sys_id) → record` lookups for the lifetime of a single request. A query that resolves the same user across 10 incidents pays one lookup, not 10.

**Concurrency limits at the proxy.** Fly's request concurrency is set to a soft cap of 20 and a hard cap of 25 per machine. Beyond the hard cap, Fly queues then rejects. Keeps a runaway client from monopolizing.

**Healthcheck with realistic grace period.** Fly's TCP healthcheck waits 120 seconds before the first probe (the lifespan warmup is async, so the app reports healthy quickly but the model is still loading). Interval 15s, timeout 5s. Pinned to always-on so the demo is never cold-started under review.

**Vercel proxy route.** The frontend talks to the backend through `/api/proxy/[...path]` on Vercel rather than calling Fly directly from the browser. This was originally a workaround for a DNS network that couldn't resolve Fly's IPv4, but the side benefits are real: the backend URL never appears in client bundles, CORS is sidestepped, and a future move to a different backend host is a one-file change.

**HTTPS-only with TLS at the edge.** `force_https = true` in fly.toml. MCP SSE on port 8001 is TLS-terminated by Fly's edge before reaching uvicorn.

**MCP transport security with deployment-hostname allowlist.** The MCP SDK auto-applies a localhost-only DNS-rebinding protection layer by default, which 421s every request behind a TLS proxy. The server is configured with an explicit `MCP_ALLOWED_HOSTS` list that includes the Fly hostname, with a regression test covering both the enabled and disabled cases.

**Structured logs.** Every request gets a request ID propagated through structlog. The frontend echoes it back via the trace inspector so a user reporting a bad answer can give an exact log handle.

**Quality gate on every push.** CI runs ruff, mypy strict, the 285-test suite, and the 3-query eval smoke. A red build blocks merge.

---

# What's beyond the four required components

The four-component spec gets a working prototype. I built more than that because Atomicwork's product is built around three things a bare-minimum prototype wouldn't show:

**HITL writes.** Atom is HITL-first. The write path here mirrors that. The planner emits a `write_proposal` op, the executor builds a per-field diff and stashes it under a one-time token, the frontend renders an approval dialog. Confirmation triggers a re-fetch and an optimistic-lock check on `sys_updated_on` before mutating. Only `create_incident` and `update_incident` are allowed, and the field set is whitelisted at the schema level. A prompt-injected "delete all incidents" cannot be represented in the plan, because the op doesn't exist.

**MCP.** The whole layer is exposed as a Model Context Protocol server with six tools: `query_itsm` (natural language), `get_incident`, `list_incidents`, `search_kb` (structured), and `propose_write`, `confirm_write` (the HITL pair). Both stdio (for Claude Desktop) and SSE (for remote agents). This positions Atomic Bridge as a tool another agent can use.

**Guardrails.** Five named defenses, each with a paper citation. Action-Selector (Beurer-Kellner et al., 2025), the Plan-Then-Execute validation gate, Dual LLM (Willison, 2023), Spotlighting + Sandwich (Liu et al., USENIX 2024), and an input validator with a `sha256`-chained audit log verified across 100 sequential entries.

There's also a 44-query gold eval suite, conversational sessions, PII redaction at the schema level, a Datadog-style trace inspector in the frontend, and live deploys. None of those are on the critical path for the four required components, so a reviewer who only wants to evaluate the spec can ignore this section.

---

# Tests & quality

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests -m "not real_llm"
```

**285 tests passing.** Covers the schema graph (entity/relation/value-map ops, BFS, subgraph expansion), the planner (mocked LLM, validator rejection paths, role enrichment), the executor (every op handler, dangling references, the KB-to-incident category bridge), guardrails (hash chain across 100 entries), MCP server (transport security regression for the Fly hostname), and the full API pipeline including the write path.

Quality gate: ruff + mypy strict + pytest, all clean. CI runs all three on every push.

## Eval

```bash
cd backend
.venv/Scripts/python.exe -m eval.runner eval/queries.yaml eval/reports/
```

44 gold queries split as 8 lookup, 6 knowledge, 6 analytical, 6 cross-reference, 3 write, 3 conversational, 4 edge cases, 5 prompt injections, 3 out-of-scope. Output is a dated markdown report plus `latest.json`. The 3-query smoke subset runs in CI on every push for free. The full run costs about $0.50.

## Project layout

```
atomic-bridge/
├── backend/
│   ├── data/                # schema.yaml + JSON fixtures
│   ├── src/
│   │   ├── core/            # schema_graph, data_store, transformations
│   │   ├── planner/         # 3 agents + prompts + few-shot library
│   │   ├── executor/        # engine + 6 op handlers + reference resolver
│   │   ├── knowledge/       # FAISS KB retriever
│   │   ├── response/        # response generator + grounding
│   │   ├── guardrails/      # 5 named defenses
│   │   ├── write_path/      # HITL propose/confirm
│   │   ├── llm/             # Anthropic + OpenAI behind one Protocol
│   │   ├── api/             # FastAPI routes + middleware + sessions
│   │   └── mcp_server/      # 6 MCP tools (stdio + SSE)
│   ├── eval/                # gold queries, runner, reports
│   └── tests/               # 285 tests
└── frontend/
    ├── app/                 # chat + schema graph viewer
    └── components/          # ChatPanel, PlanInspector (Plan/Trace/Data), ApprovalDialog
```

## Deploy

One-time setup:

```bash
fly auth login
fly apps create itsm-bridge-backend
fly volumes create itsm_data --size 1
fly secrets set ANTHROPIC_API_KEY=...
```

Then to deploy:

```bash
fly deploy --remote-only -a itsm-bridge-backend
```

The frontend deploys automatically from `main` via Vercel's GitHub integration.

## License

Take-home project. All rights reserved by the author for assignment review purposes.
