# Atomic Bridge

A natural-language layer over a ServiceNow-shaped IT data model. Built as the Atomicwork take-home.

- **Live demo:** https://atomic-bridge.vercel.app/
- **Backend API:** https://itsm-bridge-backend.fly.dev
- **MCP SSE:** https://itsm-bridge-backend.fly.dev:8001/sse

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

If you have an Anthropic key you can run the full eval (~$0.50 in credits); without one, the smoke subset and all unit/integration tests run free.

---

# Design

The PDF asks for four things in the design write-up: how the graph schema is laid out, how the planner works, the trade-offs behind the choice of graph representation, and what I'd do differently with more time. Those are the four sections below.

## The graph

The schema lives in `backend/data/schema.yaml` and gets loaded once at startup into a `networkx.MultiDiGraph`. Five entity types right now: `incident`, `sys_user`, `sys_user_group`, `kb_knowledge`, and `category`. Two value maps (one each for `state` and `priority`). Thirteen relations.

There are four kinds of node. **Entities** are tables. **Fields** are columns; each carries display name, data type, an optional sensitivity flag, and an optional `references` pointer (so `incident.caller_id` points at `sys_user`). **ValueMaps** are the integer-to-label dictionaries — modeled as first-class nodes rather than buried as constants somewhere, so the LLM can see the mapping when it reads the schema. **Relations** are named, directional, semantic verbs between two entities. They're the most important node type and the bit that took the longest to get right.

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

The planner sees `verb_phrase`. The executor walks `via_field`. Neither layer knows what the other knows. The Relation node is the bridge — that separation is the central architectural claim of the prototype.

Six edge types tie everything together. `HAS_FIELD` connects an entity to its columns. `MAPS_THROUGH` connects a field to its value map. `VIA` connects a relation to the field that physically implements it. `FROM` and `TO` connect a relation to its two endpoints. `INVERSE_OF` connects a relation to its dual.

The PDF specifically calls out three relationships that "should be edges":

1. `incident.caller_id → user` — present as `incident.reportedBy`.
2. `incident.assignment_group → group` — present as `incident.handledBy`.
3. *"A KB article's kb_category maps to an incident's category"* — present as a four-relation bridge through a new `category` entity.

That third one is worth a paragraph. Categories were originally plain strings on both incidents and KB articles. I made `Category` a first-class entity late in the build, with one trick: the `sys_id` of each Category record is the category name itself ("Network", "Software", etc.). That means the existing `incident.category="Network"` already references the right record — no data files changed, only `schema.yaml` and a derived `categories.json`. The bridge is now a real graph walk: `kb → category → incidents in that category`. Test: `test_kb_to_incidents_via_category_bridge`.

### How the graph gets traversed

At planning time, the planner is given a markdown serialization of the schema produced by `SchemaGraph.to_llm_context()`. The preprocessor returns a list of `relevant_entities` based on the user's question, and the plan generator runs `expand_subgraph(seeds, max_hops=2)` over the relation graph in both directions before serializing. That second step matters. The naive version, which I shipped first, would only show the planner the entities it directly named — so if the preprocessor said `[sys_user]` for "Ravi's team," the planner wouldn't see `sys_user_group` in its context and couldn't emit `incident.handledBy`. At our scale of 5 entities the BFS expansion is effectively a no-op, but at 50+ tables it's the only way to keep the planner's context tractable without giving up reachability.

At execution time, the engine walks Relation nodes directly. The traverse handler reads `cardinality` and decides whether to gather sys_ids and call `store.get_many`, or do the inverse path with `store.find(target_entity, [Filter on via_field in source_ids])`. The handler has zero semantic knowledge — it doesn't know that "manages" means anything. Knowing what verb to walk was the planner's job; following an edge is all the executor does.

## The planner

Three agents. Preprocessor and plan generator are each one LLM call. The validator is pure Python.

**Preprocessor.** Classifies intent into one of seven values (`lookup`, `knowledge`, `analytical`, `cross_reference`, `write_proposal`, `ambiguous`, `out_of_scope`). Extracts entity mentions. Resolves pronouns against the prior turn. Returns the relevant subgraph.

Before the LLM call, a deterministic `NameResolver` runs and pre-attaches candidate matches to the prompt — fuzzy match on user names, including a token-prefix tier so "John D." matches "John Doe." The LLM disambiguates rather than recalls. After the LLM call, a post-processing step enriches every resolved `sys_user` mention with `department`, `location`, `direct_reports`, and `groups_managed` from the data store. That enrichment carries real weight; see below.

**Plan generator.** One LLM call via tool use. The output is a `QueryPlan`, schema-constrained by Pydantic. Six op types: `find`, `traverse`, `aggregate`, `kb_lookup`, `resolve`, `write_proposal`. Each op has an `id` that later ops reference as `$id`. Few-shot examples are retrieved by query-embedding similarity from a YAML library.

**Plan validator.** Pure Python. Verifies every entity/field/relation in the plan exists. Verifies every `$var` reference is defined upstream. Enforces resource limits. Rejects plans where `write_proposal` isn't the last op. It also *transforms* the plan: filter values for value-mapped fields are converted from display strings to integer codes ("In Progress" → 2), and `is_sensitive` fields are stripped from `resolve.fields` unless the session has `view_pii`. Rejection is hard — the API returns a 400 with the validator errors. No silent retry, because retries hide injection attempts.

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

Three reasons. A plan is **inspectable** — the frontend renders it before the answer arrives, and the user can see what the system decided to do. It's **replayable** — the same plan against the same data is deterministic, so the eval suite can match on substrings without flake. And it's **replaceable** — a fine-tuned classifier could emit the same JSON shape tomorrow with no changes downstream. None of that is true for a tool-call loop, where the model's choices are scattered across multiple turns and only legible through trace logs.

### The role-aware disambiguation

This is the part I rebuilt twice during the build. Originally the planner had a fixed rule for ambiguous possessives: "X's tickets" defaults to caller. That works for John Doe in Engineering. It returns zero rows for any IT-side user — Ravi (IT Support), Priya (IT Support), Mike (Facilities) all have zero reported incidents and only handle tickets. So the bare phrase "Show me Ravi's tickets" silently returned nothing.

The first patch was a few-shot example for Ravi specifically. That fixed Ravi and left Priya and Mike still broken. The second patch was to enrich the preprocessor output with department info and write a department-aware prompt rule: IT-side users default to assignee, others to caller. That handles all six users with one rule.

Then "Deepak Sharma's team" broke for a related reason. Deepak is a director — he manages three reports and five assignment groups, and has zero of his own assignments. "His team's incidents" can't go through his (empty) ticket queue. So the enrichment also surfaces `direct_reports` and `groups_managed` counts, and the prompt has a three-way dispatch: if `groups_managed > 0`, walk `sys_user.managesGroups`; else if `direct_reports > 0`, walk `sys_user.manages`; else walk the staff path. All three branches have a few-shot example.

The lesson here, in case it's useful for whoever's reviewing: this kind of bug is invisible until the user types the right phrasing. The mock data has both end-users and agents because it's a realistic mock, and the planner needs role context to disambiguate possessives. I doubt this is the last edge case of this kind in the data, but the pattern — surface role context from the preprocessor, let the planner pick the relation — generalizes.

## Trade-offs in graph representation

I considered three approaches when starting.

**Neo4j with the LLM emitting Cypher.** Most authentic. The LLM emits Cypher, Neo4j handles join planning, you get a real query language for free. I ruled it out for three reasons. First, Cypher injection is harder to gate than a structured plan — the validator I wrote is 250 lines of Pydantic and dispatch, and equivalent Cypher analysis would be a small static analyzer in its own right. Second, Neo4j adds a service to deploy and a license to track for a take-home that doesn't need either. Third, and the one that decided it, I wanted the schema to live in the application process so it could be loaded from YAML, traversed deterministically by Python, and tested by reading objects rather than running queries. Neo4j would push schema knowledge into a database, which is the wrong locus of control for a prototype meant to make the schema *easy to reason about*.

**A plain adjacency dict.** Where I started. Looked like `{entity: {relation_name: target_entity}}`. Worked for one-hop. Broke when I needed to ask, "what's the cardinality of this relation in the reverse direction?" — the kind of question that needs relation metadata, not just topology. I could have added a parallel `relations_meta` dict and a `field_meta` dict and a `value_maps` dict, but at some point you've reinvented a graph library and done it badly.

**NetworkX `MultiDiGraph`.** Where I landed. It gives me directed edges with typed attributes, parallel edges between the same node pair (useful for the `INVERSE_OF` edges that share endpoints with the relations they invert), and a BFS primitive I use for `shortest_relation_path` and `expand_subgraph`. It's in-process — no service to deploy, no migrations, nothing to operate. The cost is that the graph isn't queryable in a structured language. I can't hand a reviewer a Cypher query and say "this is what the planner does." Instead I serialize the (filtered) graph to a markdown blob via `to_llm_context()` and pass that to the LLM. It's a real serialization step, and it's the main thing I'd revisit if I needed to scale this graph past 50 tables — at that point the planner's context window starts to matter and I'd want some kind of retrieval-based subgraph selection on top of the structural BFS expansion.

A practical consequence of choosing in-process NetworkX is that the whole stack is one container. The backend cold-starts in about 2 seconds. The schema is a YAML file. Adding a new ITSM table is a YAML edit, demonstrated mechanically by `test_adding_entity_to_yaml_is_reflected_without_code_changes` — that test parses the production schema, appends a new `change_request` entity, reloads, and asserts it appears in the planner's prompt. No Python changed.

## What I'd do differently

A few items, ranked by what I think would move the needle most.

**Stronger grounding on the response side.** The response generator is well-defended by the Dual LLM separation (planner can't see data, response generator has no tools) and by spotlighting and sandwich prompting. But the grounding check is regex-based — it scans the response for invented INC/KB numbers and sys_ids that aren't in the executor's outputs. That catches the obvious cases but would miss a hallucinated user name like "John Smith" if the actual data only had "John Doe." The right pattern is extracting entity references from the response and verifying each one. I drafted it but didn't ship.

**Category as a hierarchy, not a flat list.** Right now `Category` has no parent/child relations. In a real ServiceNow instance, categories tree (Cloud Services → AWS → S3). Adding that is a single self-referential relation on the entity, but doing it well means rethinking how the KB retriever does category matching when the user mentions a parent and you want to return children too.

**Table inheritance.** ServiceNow uses single-table inheritance heavily (`task → incident, problem, change_request`). My schema is flat. A real production extension would need an `INHERITS_FROM` edge type and an executor that understands a query on `task` should fan out across children. Out of scope for a prototype, but it's the schema change that matters most when scaling beyond a single table type.

**Date-range filters.** No current-time injection. "Incidents this week" doesn't translate. The planner has `gt`/`lt` operators on datetime fields but no notion of `now`. It's the smallest meaningful production add — maybe two hours of work if I knew the timezone story I wanted.

**SSE streaming on `/v1/query`.** The original spec called for the frontend to receive the plan as soon as it was generated, then trace steps as the executor walked them, then the answer. I built the frontend with POST and never went back. That's the change that would visibly improve perceived latency the most. Right now you wait 5–10 seconds with a shimmer; with streaming you'd see the plan render after 1.5 seconds and trace fill in live.

**A nightly eval gate.** I have a 44-query gold set but CI only runs the 3-query smoke subset for free. A nightly job running the full eval against a stable model snapshot would give a real regression signal instead of "the unit tests pass." That's the gap between "the code is correct" and "the system works."

---

# What's beyond the four required components

I built more than the spec strictly requires because Atomicwork's product is built around three things the bare-minimum prototype wouldn't show:

**HITL writes.** Atom is HITL-first. The write path here mirrors that: the planner emits a `write_proposal` op, the executor builds a per-field diff and stashes it under a one-time token, the frontend renders an approval dialog. Confirmation triggers a re-fetch and an optimistic-lock check on `sys_updated_on` before mutating. Only `create_incident` and `update_incident` are allowed, and the field set is whitelisted at the schema level. A prompt-injected "delete all incidents" cannot be represented in the plan, because the op doesn't exist.

**MCP.** The whole layer is exposed as a Model Context Protocol server with six tools: `query_itsm` (natural language), `get_incident` / `list_incidents` / `search_kb` (structured), and `propose_write` / `confirm_write` (the HITL pair). Both stdio (for Claude Desktop) and SSE (for remote agents). This positions Atomic Bridge as a tool another agent can use — Atom included.

**Guardrails.** Five named defenses, each with a paper citation: Action-Selector (Beurer-Kellner et al., 2025), the Plan-Then-Execute validation gate, Dual LLM (Willison, 2023), Spotlighting + Sandwich (Liu et al., USENIX 2024), and an input validator with a `sha256`-chained audit log verified across 100 sequential entries.

There's also a 44-query gold eval suite, conversational sessions, PII redaction at the schema level, a Datadog-style trace inspector in the frontend, and live deploys. None of those are on the critical path for the four required components — a reviewer who only wants to evaluate the spec can ignore everything in this section.

---

# Tests & quality

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests -m "not real_llm"
```

**280 tests passing.** Covers the schema graph (entity/relation/value-map ops, BFS, subgraph expansion), the planner (mocked LLM, validator rejection paths, role enrichment), the executor (every op handler, dangling references, the KB-to-incident category bridge), guardrails (hash chain across 100 entries), MCP server (transport security regression for the Fly hostname), and the full API pipeline including the write path.

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
│   └── tests/               # 280 tests
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
