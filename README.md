# Atomic Bridge

A natural-language layer over a ServiceNow-shaped IT data model. Built as the Atomicwork take-home.

## The fastest way to evaluate this

The system is hosted, so you don't need to clone, install, or run anything. Open the live demo and start typing:

> **Live demo: https://atomic-bridge.vercel.app/**

- **Backend API:** https://itsm-bridge-backend.fly.dev (FastAPI, health at `/health`)
- **MCP SSE endpoint:** https://itsm-bridge-backend.fly.dev:8001/sse (six tools, ready for any MCP client)

The starter gallery in the demo covers the four query categories the PDF asks about, plus the trickier ones (a 3-hop walk, an ambiguous bare-name query, a self-relation walk for "Ravi's manager", and the HITL write flow). Local setup is at the bottom if you want it, but the hosted demo is the recommended path. The Fly machine is pinned to always-on (`min_machines_running = 1`), so there's no cold start on the first request.

> The live demo ships with a **persona picker** in the header. Click it for a searchable list of every user in the data, each tagged with a server-derived role: end_user, agent, manager, or admin. Admin keeps full access. End users see only their own tickets. Agents see their queue plus their groups' workload. Managers see their direct reports' tickets. Switch personas and the same query plays out differently. See the [Role-based view control](#role-based-view-control) section for the visibility matrix and how it's enforced.

ServiceNow stores IT data with `sys_id` references, integer-coded statuses, and field names like `assignment_group`. The user thinks in employees, tickets, severity, teams. This is the layer that translates between the two, plans the work as a structured operation list, and only then executes against the data.

## Stack

Backend is Python 3.12 with FastAPI, Pydantic v2, NetworkX, FAISS, sentence-transformers, and the official `mcp` SDK. Frontend is Next.js 14 with Tailwind and vis-network. LLMs are Anthropic Claude by default and OpenAI as the alternate, both behind one Protocol. Deploys go to Vercel for the frontend and Fly.io for the backend (one container, two services: FastAPI on 8000, MCP SSE on 8001).

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

The first run downloads about 80MB of `all-MiniLM-L6-v2` weights into `~/.cache/huggingface`. After that it's cold-to-ready in about 2 seconds.

If you have an Anthropic key you can run the full eval (~$0.50 in credits). Without one, the smoke subset and all unit/integration tests run free.

---

# Design

The PDF asks for four things in the design write-up: how the graph schema is laid out, how the planner works, the trade-offs behind the choice of graph representation, and what I'd do differently with more time. Those are the four sections below.

## The graph

The schema lives in `backend/data/schema.yaml` and gets loaded once at startup into a `networkx.MultiDiGraph`. Five entity types (`incident`, `sys_user`, `sys_user_group`, `kb_knowledge`, `category`). Two value maps (one each for `state` and `priority`). Fourteen relations, and every relation has a first-class inverse. A schema invariant test guards future YAML edits.

There are four kinds of node. **Entities** are tables. **Fields** are columns: each carries a display name, a data type, an optional sensitivity flag, and an optional `references` pointer (so `incident.caller_id` points at `sys_user`). **ValueMaps** are the integer-to-label dictionaries, modeled as first-class nodes rather than buried as constants somewhere, so the LLM can see the mapping when it reads the schema. **Relations** are named, directional, semantic verbs between two entities.

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

The planner sees `verb_phrase`. The executor walks `via_field`. Neither layer knows what the other knows, and the Relation node is the bridge between them. That separation is the central architectural claim of the system.

### The graph IS the executor

`SchemaGraph` is not just metadata. It owns the data store and the KB retriever (attached via `bind_runtime(store, kb)`) and exposes the execution methods directly: `find`, `walk`, `aggregate`, `resolve`, `kb_search`, `propose_write`, plus an `execute_plan` dispatcher that walks a `QueryPlan` op by op. There is no separate executor module. The plan is a script over the graph's functions, and the runtime "graph-ness" (shortest-path enumeration, chain validation, per-hop filters, ranked fallback) happens where the work happens, not in a flat config consulted at planning time.

Six edge types tie everything together. `HAS_FIELD` connects an entity to its columns. `MAPS_THROUGH` connects a field to its value map. `VIA` connects a relation to the field that physically implements it. `FROM` and `TO` connect a relation to its two endpoints. `INVERSE_OF` connects a relation to its dual.

The PDF specifically calls out three relationships that "should be edges":

1. `incident.caller_id → user`. Present as `incident.reportedBy`.
2. `incident.assignment_group → group`. Present as `incident.handledBy`.
3. *"A KB article's kb_category maps to an incident's category"*. Present as a four-relation bridge through a `category` entity.

The third one is worth a paragraph. `Category` is a first-class entity with one trick: the `sys_id` of each Category record is the category name itself (`"Network"`, `"Software"`, etc.). That means an existing `incident.category="Network"` already references the right record. No data files changed, only `schema.yaml` and a derived `categories.json`. The bridge is a real graph walk: `kb → category → incidents in that category`. Test: `test_kb_to_incidents_via_category_bridge`.

### How traversal happens

At planning time the planner is given a markdown serialization of the schema, produced by `SchemaGraph.to_llm_context()`. The planner emits a declarative traverse op that names the *target* entity plus the relation chain it wants to walk. At execution time, `SchemaGraph.walk` calls into the data store along that chain, applying per-entity filters at the hop where each filter's target entity appears.

When the planner is genuinely unsure which of several shortest chains is the right one, it can leave the chain off and let the engine ask the response-side LLM to rank the candidates. The engine walks the ranking; if the top-ranked chain returns zero records, it falls back to the next-ranked, capped at four attempts. Every chain it tried lands in the trace, so a user can see exactly what happened and why a particular result came back.

The executor itself has zero semantic knowledge. It doesn't know that "manages" means anything. Knowing what verb to walk was the planner's job. Following an edge is all the executor does.

## The planner

Two LLM calls total: Planner and Responder. No preprocessor, no few-shot library, no application-layer name index. The planner makes one privileged tool call. The responder makes one quarantined text call.

**Planner** (`backend/src/planner/planner.py`). One LLM call. Receives the schema markdown, an optional prior turn for pronoun resolution, and the user query. Emits a `QueryPlan` via tool use, schema-constrained by Pydantic. Six op types: `find`, `traverse`, `aggregate`, `kb_lookup`, `resolve`, `write_proposal`. Each op has an `id` that later ops reference as `$id`. Name lookups go through the data store via `find sys_user filters=[name contains "..."]`, so there's no application-layer fuzzy index that doesn't scale past 10k users.

The planner emits the declarative `traverse` shape: `to_entity` (the target entity), `path` (the relation chain), and `filters_by_entity` (mid-hop filters keyed by entity). It picks the path among the shortest chains based on the verb phrases in the schema. When the user's wording is genuinely ambiguous, the planner sets `intent=ambiguous` and surfaces a clarification rather than guessing.

**Plan validator.** Pure Python. Verifies every entity, field, and relation in the plan exists. Verifies every `$var` reference is defined upstream. Runs `graph.validate_path` on every declarative traverse and rejects any chain that doesn't connect, doesn't end at `to_entity`, references an invented relation, or is longer than `MAX_PATH_HOPS=4`. Enforces resource limits (`MAX_OPERATIONS=10`, `MAX_TRAVERSE_OPS=5`). Also transforms the plan: filter values for value-mapped fields are converted display to code (`"In Progress"` becomes `2`), and sensitive fields are stripped from `resolve.fields` unless the session has `view_pii`. Rejection is hard. The API returns a 400 with the validator errors. No silent retry, because retries hide injection attempts.

A worked plan for *"Show me open incidents raised by people in Engineering":*

```json
{
  "intent": "cross_reference",
  "reasoning": "Find Engineering users, declaratively reach open incidents via the reported chain.",
  "operations": [
    {"op": "find", "id": "u", "entity": "sys_user",
     "filters": [{"field": "department", "operator": "eq", "value": "Engineering"}]},
    {"op": "traverse", "id": "i", "from": "$u",
     "to_entity": "incident",
     "path": ["sys_user.incidentsReported"],
     "filters_by_entity": {
       "incident": [{"field": "state", "operator": "in",
                     "value": ["New", "In Progress", "On Hold"]}]
     }},
    {"op": "resolve", "id": "out", "source": "$i",
     "fields": ["number", "state", "priority"], "include_relations": ["reportedBy"]}
  ],
  "output_spec": {"format": "list", "final_var": "out", "max_items_shown": 20},
  "confidence": 0.92
}
```

The reviewer's hero example, *"KB articles relevant to incidents Ravi's team is handling"*, becomes a single declarative traverse with a 3-hop path:

```json
{"op": "traverse", "id": "kb", "from": "$u",
 "to_entity": "kb_knowledge",
 "path": ["sys_user.incidentsAssigned",
          "incident.inCategory",
          "category.kbArticlesInCategory"]}
```

The engine resolves the chain. The validator confirms it's a shortest path. The trace records the full chain end to end.

**Responder** (`backend/src/response/responder.py`). One LLM call. Receives the user's query, the plan, and the executor's outputs. Writes the answer in plain English. The responder has no tools and no access to anything beyond what the executor returned, so it can't reach back into the database under its own authority. Defended by spotlighting and sandwich prompting against injection that rides in on user-visible record fields.

### Why a plan, not a tool-call loop

A plan is inspectable, so the frontend renders it before the answer arrives and the user sees what the system decided to do. It's replayable, so the eval suite can match on substrings without flake (the same plan against the same data is deterministic). And it's replaceable: a fine-tuned classifier could emit the same JSON shape tomorrow and nothing downstream would care. None of that is true for a tool-call loop, where the model's choices are scattered across multiple turns and only legible through trace logs.

### Ambiguous-when-uncertain

Phrases like "X's tickets" without a verb are genuinely ambiguous against this schema. Both `sys_user.incidentsReported` (caller) and `sys_user.incidentsAssigned` (assignee) are 1-hop chains from `sys_user` to `incident`. An earlier design tried to disambiguate by looking up the user's `department` and applying a hard-coded rule (IT-side users got the assignee chain, everyone else got the caller chain). It silently broke for half the users.

The new design doesn't guess. The planner sets `intent=ambiguous` with a clarification ("Do you mean tickets Ravi raised, or tickets currently assigned to Ravi?"). When the user's wording IS qualified, with verbs like "raised", "reported", "submitted" on one side and "assigned to", "working on", "handling" on the other, the planner picks the matching chain confidently. The frontend renders the clarification as click-able buttons so the user picks one without retyping.

## Trade-offs in graph representation

Three options were on the table.

**Neo4j with the LLM emitting Cypher.** The most authentic option. The LLM emits Cypher, Neo4j handles join planning, you get a real query language for free. I ruled it out for three reasons. Cypher injection is harder to gate than a structured plan. The validator I wrote is about 250 lines of Pydantic and dispatch; equivalent Cypher analysis would be a small static analyzer in its own right. Neo4j also adds a service to deploy and a license to track for a take-home that doesn't need either. The decisive reason though was that I wanted the schema to live in the application process so it could be loaded from YAML, traversed deterministically by Python, and tested by reading objects rather than running queries. Neo4j would push schema knowledge into a database, which is the wrong locus of control for a prototype meant to make the schema *easy to reason about*.

**A plain adjacency dict.** What I started with. Looked like `{entity: {relation_name: target_entity}}`. Worked for one hop. Broke when I needed to ask "what's the cardinality of this relation in the reverse direction?", the kind of question that needs relation metadata, not just topology. I could have added a parallel `relations_meta` dict and a `field_meta` dict and a `value_maps` dict, but at some point you've reinvented a graph library and done it badly.

**NetworkX `MultiDiGraph`.** Where I landed. Directed edges with typed attributes, parallel edges between the same node pair (useful for `INVERSE_OF` edges that share endpoints with the relations they invert), and a BFS primitive used for `shortest_relation_paths` and `validate_path`. It's in-process: no service to deploy, no migrations, nothing to operate. The cost is that the graph isn't queryable in a structured language. You can't hand a reviewer a Cypher query and say "this is what the planner does." Instead the (filtered) graph gets serialized to a markdown blob via `to_llm_context()` and passed to the LLM. That serialization step is the main thing I'd revisit if the graph needed to scale past 50 tables. At that point the planner's context window starts to matter and a retrieval-based subgraph selection would sit on top of the structural enumeration.

A practical consequence of choosing in-process NetworkX is that the whole stack is one container. The backend cold-starts in about 2 seconds. The schema is a YAML file. Adding a new ITSM table is a YAML edit, demonstrated mechanically by `test_adding_entity_to_yaml_is_reflected_without_code_changes`. That test parses the production schema, appends a new `change_request` entity, reloads, and asserts it appears in the planner's prompt. No Python changed.

## What I'd do differently

A few items, ranked by what would move the needle most.

**Stronger grounding on the response side.** The responder is well-defended by the Dual LLM separation (planner has tools but no data, responder has data but no tools) and by spotlighting and sandwich prompting. The grounding check itself is regex-based: it scans the response for invented INC and KB numbers and sys_ids that aren't in the executor's outputs. That catches the obvious cases but would miss a hallucinated user name like "John Smith" if the actual data only had "John Doe". The right pattern is extracting entity references from the response and verifying each one against the executor's outputs.

**Category as a hierarchy, not a flat list.** Right now `Category` has no parent/child relations. In a real ServiceNow instance categories tree (Cloud Services > AWS > S3). Adding that is a single self-referential relation on the entity, but doing it well means rethinking how the KB retriever does category matching when the user mentions a parent and you want children too.

**Table inheritance.** ServiceNow uses single-table inheritance heavily (`task` is the parent of `incident`, `problem`, `change_request`). My schema is flat. A real production extension would need an `INHERITS_FROM` edge type and an executor that understands a query on `task` should fan out across children. Out of scope for a prototype, but it's the schema change that matters most when scaling beyond a single table type.

**Date-range filters.** No current-time injection. "Incidents this week" doesn't translate. The planner has `gt` and `lt` operators on datetime fields but no notion of `now`. Small change to add, valuable for any reporting use case.

**SSE streaming on `/v1/query`.** Right now the frontend submits a POST and waits for the full response (plan plus trace plus answer) before rendering anything. Streaming would let the plan render as soon as it was generated, then trace steps as the executor walked them, then the answer. The change that would visibly improve perceived latency the most.

**Real auth.** Today the persona picker is client-side: the frontend sends a `role` and an `as_user_sys_id` in the create-session body, and the backend trusts both. That's fine for a take-home demo, since the point is to show the visibility model working end to end. A real deployment would tie the session to an SSO identity (Okta, Azure AD) and derive the role + group memberships from the IdP, not from the request body. Everything *downstream* of identity already works, see [Role-based view control](#role-based-view-control), so the swap is contained to the session-create handler.

---

# Role-based view control

The four roles and the records each one can see:

| Role | Demo persona | Visible incidents | Visible users | Visible groups | Aggregate | Write proposal |
|------|--------------|-------------------|---------------|----------------|-----------|----------------|
| **end_user** | John Doe | `caller_id = self` | self only | none | no | own tickets only |
| **agent** | Ravi Kumar | `assigned_to = self` OR `assignment_group IN session.groups` | self + group members + own manager | groups they're a member of | yes | visible tickets |
| **manager** | Deepak Sharma | caller or assignee in `{self ∪ direct reports}` | self + direct reports + own manager | groups they manage | yes | visible tickets |
| **admin** | Service Desk Admin | all | all | all | yes | all |

KB articles and categories are public for every role. Sensitive fields (today: `sys_user.email`) are redacted for everyone except admin.

The matrix lives in [`backend/src/guardrails/view_scope.py`](backend/src/guardrails/view_scope.py) and is enforced by three layers that all consult the same module:

1. **Planner-aware.** The planner gets a `## View scope` block in its prompt that names the actor and lists what they can see. It emits a scope-aligned plan, or sets `intent=out_of_scope` with a one-line clarification when the request clearly exceeds the role. This is the best UX path: the LLM doesn't even try the forbidden thing.

2. **Validator rejects.** The plan validator catches anything the planner missed. Listing the full user table from an end_user session, aggregating from an end_user session, and a few other clear violations get a 400 with a readable error. No silent retry. The validator is loud on purpose.

3. **Executor row-level filter.** The silent last-line guarantee. After every `find` and after every hop in `walk`, the scope's predicate runs over the resulting records and drops anything the actor can't see. Admin is a no-op. Group-scoped agents get records filtered by their precomputed `visible_user_sys_ids` set, computed once at session-create time.

Identity is precomputed at session-create. POST `/v1/session` accepts `{role?, as_user_sys_id?}`. When `as_user_sys_id` is set without a role, the server derives it from the user's data (manager > agent > end_user); an explicit `role` overrides the derivation. The handler resolves the actor's groups, direct reports, managed groups, and visible-users set in one pass. The session is the source of truth for visibility; subsequent queries just consult it.

`GET /v1/personas` returns the synthetic admin row plus every active user with their derived role, member groups, direct-report count, and managed-group count. The frontend uses this for the persona picker so a reviewer can switch to any actor in the data, not a hardcoded shortlist.

Tests: 28 unit tests in [`test_view_scope.py`](backend/tests/unit/test_view_scope.py) cover the matrix and `derive_role` directly. Ten integration tests in [`test_role_visibility.py`](backend/tests/integration/test_role_visibility.py) exercise the full pipeline (planner -> validator -> executor) per role, including the validator rejection paths and PII stripping. Three more in [`test_api.py`](backend/tests/integration/test_api.py) cover the `/v1/personas` endpoint and the role-derivation / role-override behaviour at session-create time.

What's NOT in this build, deliberately:

- **Real auth.** The frontend tells the backend which persona to act as. A real deployment would derive identity from SSO, not the request body. The session-create handler is the contained change.
- **Per-field permissions beyond `is_sensitive`.** A field is either sensitive (redacted for non-admin) or not. Production ServiceNow has richer ACLs; the schema and `OutputFilter` could grow them without restructuring.
- **Per-record ACLs.** "This incident is locked to the security team" is not modeled. Possible as another field on the matrix.
- **Manager-of-manager hierarchy.** Managers see direct reports, not reports-of-reports. Tree traversal is a future iteration.

---

# Fallbacks and limits

**Cross-provider LLM fallback.** Every LLM client is wrapped in a `FallbackLLMClient` when both API keys are set. The primary provider (Anthropic by default) runs first. On a terminal failure after its retry loop is exhausted (a 12-minute OpenAI outage, Anthropic silently dropping requests, etc.), the secondary provider runs the same call once. `LLMToolCallMissingError` is explicitly *not* failed over because it signals a planner-prompt bug rather than a provider issue, and switching models would mask the real cause. Toggle with `LLM_ENABLE_FALLBACK`. See `backend/src/llm/fallback.py` and the four tests in `test_llm.py` under "Fallback wrapping".

**Retry with exponential backoff and jitter.** `LLMRateLimitError` and `LLMTransientError` (which wraps `APITimeoutError`, `APIConnectionError`, and `InternalServerError` from both SDKs) are retried up to 3 times with delays of 1, 2, and 4 seconds plus 25% jitter. Capped at 16s per attempt. Other LLM errors propagate immediately so the request fails fast rather than hanging the client. See `backend/src/llm/retry.py`.

**Token-bucket rate limiting.** 60 requests per minute per client IP, configurable via `RATE_LIMIT_PER_MIN`. Returns HTTP 429 with a clear reason. See `backend/src/api/rate_limit.py`.

**Session TTL.** Conversation context expires after 30 minutes of inactivity (`SESSION_TTL_SECONDS=1800`). Sessions are an in-memory ring buffer of recent turns; the planner consumes the prior turn to resolve pronouns.

**Write-proposal expiry and optimistic locking.** Proposals expire after 10 minutes (`PROPOSAL_TTL_SECONDS=600`). On confirm, the handler re-fetches the target and checks `sys_updated_on` against the snapshot taken at propose time. If another write landed in between, the confirm fails with a clean conflict and the user re-issues the proposal. No silent overwrites.

**Hash-chained audit log.** Every request is appended to an NDJSON log with `sha256(prev_hash + entry)` chaining. Tampering anywhere in the chain breaks every subsequent hash. Verified across 100 sequential entries by `test_guardrails.py`.

**Input validation, fail-fast.** Length cap (5000 chars), Unicode NFKC normalization, banned-substring scan, and an advisory injection-pattern detector (it scores but doesn't block, since the Dual LLM separation is the real defense). Bad input returns HTTP 400 before any LLM is touched.

**Lazy ML loading.** The sentence-transformer model and FAISS KB index are not loaded in the request path. The model loads on first `embed()` call and the index builds on first `search()`. A FastAPI lifespan background task preloads both 5 to 15 seconds after boot, so the first real request never pays the warmup cost. The `/health` endpoint returns in about 2 seconds while warmup is still in flight.

**Reference-resolver cache.** `(entity, sys_id) → record` lookups are memoized for the lifetime of a single request. A query that resolves the same user across 10 incidents pays one lookup, not 10.

**MCP transport security with deployment-hostname allowlist.** The MCP SDK auto-applies a localhost-only DNS-rebinding protection layer by default, which 421s every request behind a TLS proxy. The server is configured with an explicit `MCP_ALLOWED_HOSTS` list that includes the Fly hostname, with a regression test covering both the enabled and disabled cases.

**Structured logs.** Every request gets a request ID propagated through structlog. The frontend echoes it back via the trace inspector so a user reporting a bad answer can give an exact log handle.

**Quality gate on every push.** CI runs ruff, mypy strict, and the full unit-test suite. A red build blocks merge.

---

# What I added on top

The four-component spec gets a working prototype. I built more than that because Atomicwork's product is built around three things a bare-minimum prototype wouldn't show:

**Role-based view control.** Four roles (end_user, agent, manager, admin) with three enforcement layers. The planner sees a view-scope block in its prompt and emits a scope-appropriate plan, or sets `intent=out_of_scope` when the user's request clearly exceeds the role. The plan validator hard-rejects clear violations (an end user trying to enumerate the user table, an end user asking to aggregate). The executor applies a row-level filter after every `find` and after every hop in `walk`, so chains that pass through out-of-scope records get pruned silently. Three layers, one matrix in [`guardrails/view_scope.py`](backend/src/guardrails/view_scope.py), with 38 tests across unit and integration. The persona picker in the header reads `/v1/personas` and lists every user in the data with a server-derived role, so a reviewer can switch to any actor and watch the same query play out across views.

**HITL writes.** Atomicwork's Atom is HITL-first. The write path here mirrors that. The planner emits a `write_proposal` op, the executor builds a per-field diff and stashes it under a one-time token, the frontend renders an approval dialog. Confirmation triggers a re-fetch and an optimistic-lock check on `sys_updated_on` before mutating. Only `create_incident` and `update_incident` are allowed, and the field set is whitelisted at the schema level. End users can propose writes only on their own tickets (validator-checked at plan time, executor-checked at confirm time). A prompt-injected "delete all incidents" cannot be represented in the plan, because the op doesn't exist.

**MCP.** The whole layer is exposed as a Model Context Protocol server with six tools: `query_itsm` (natural language), `get_incident`, `list_incidents`, `search_kb` (structured), and `propose_write`, `confirm_write` (the HITL pair). Both stdio (for Claude Desktop) and SSE (for remote agents). This positions Atomic Bridge as a tool another agent can use.

**Guardrails.** Five named defenses, each with a paper citation. Action-Selector (Beurer-Kellner et al., 2025), the Plan-Then-Execute validation gate, Dual LLM (Willison, 2023), Spotlighting + Sandwich (Liu et al., USENIX 2024), and an input validator with a `sha256`-chained audit log verified across 100 sequential entries.

There's also a 44-query gold eval suite, a Datadog-style trace inspector in the frontend with an inline schema graph view that lights up the walked chain in red, and live deploys. None of those are on the critical path for the four required components, so a reviewer who only wants to evaluate the spec can ignore this section.

---

# Tests & quality

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests -m "not real_llm"
```

About 400 tests passing. Covers the schema graph (entity, relation, value-map ops, BFS, shortest-path enumeration, path validation, the schema invariant that every relation has an inverse), the planner (mocked LLM, validator rejection paths, ambiguous-on-bare-phrasing), the engine (every op, dangling references, the KB-to-incident category bridge, the multi-path walk with ranked fallback when the planner leaves `path` empty, the explicit-path-no-fallback rule, the self-relation walk for "Ravi's manager"), guardrails (hash chain across 100 entries, the role-based view-scope matrix, persona-role derivation), MCP server (transport security regression for the Fly hostname), and the full API pipeline including the write path and `/v1/personas`.

Quality gate: ruff + mypy strict + pytest, all clean. CI runs the lint, type check, and unit tests on every push.

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
│   │   ├── core/            # schema_graph (the executor), data_store, trace, filters, transformations
│   │   ├── planner/         # planner + plan_schema + plan_validator + relation_scorer + prompts
│   │   ├── response/        # responder + prompt
│   │   ├── knowledge/       # FAISS KB retriever
│   │   ├── guardrails/      # 5 named defenses
│   │   ├── write_path/      # HITL propose/confirm
│   │   ├── llm/             # Anthropic + OpenAI behind one Protocol, with retry + fallback
│   │   ├── api/             # FastAPI routes + middleware + sessions
│   │   └── mcp_server/      # 6 MCP tools (stdio + SSE)
│   ├── eval/                # gold queries, runner, reports
│   └── tests/               # ~400 tests
└── frontend/
    ├── app/                 # chat + schema graph viewer
    └── components/          # ChatPanel, PlanInspector (Plan/Trace/Data/Graph), ApprovalDialog
```

## Deploy

One-time setup:

```bash
fly auth login
fly apps create itsm-bridge-backend
fly volumes create itsm_data --size 1
fly secrets set ANTHROPIC_API_KEY=...
```

Then redeploys:

```bash
fly deploy --remote-only -a itsm-bridge-backend
```

The frontend deploys automatically from `main` via Vercel's GitHub integration.

## License

Take-home project. All rights reserved by the author for assignment review purposes.
