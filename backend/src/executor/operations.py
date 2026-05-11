from __future__ import annotations

import time
from collections import Counter
from typing import Any

from src.core.data_store import DataStore, Filter, Record
from src.core.in_memory_store import apply_filters
from src.core.schema_graph import SchemaGraph
from src.executor.reference_resolver import ReferenceResolver
from src.executor.trace import TraceStep
from src.knowledge.retriever import KBRetriever
from src.planner.plan_schema import (
    AggregateOp,
    FindOp,
    KBLookupOp,
    Operation,
    ResolveOp,
    TraverseOp,
    WriteProposalOp,
)
from src.write_path.proposal import WriteProposal, new_proposal

# ---------------------------------------------------------------------------
# Internal context. Engine owns this; handlers read/write through it.
# ---------------------------------------------------------------------------


class ExecutionContext:
    def __init__(self) -> None:
        # var_id -> records (for find/traverse/kb_lookup) OR a scalar/dict (aggregate)
        self.bindings: dict[str, Any] = {}
        # var_id -> entity name (so resolve knows which entity its source records are)
        self.var_entity: dict[str, str] = {}
        self.warnings: list[str] = []


# ---------------------------------------------------------------------------
# Handler functions. Each returns (result, TraceStep). The engine assigns
# the result to ctx.bindings[op.id] and appends the trace step.
# ---------------------------------------------------------------------------


def find_handler(
    op: FindOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[list[Record], TraceStep]:
    t0 = time.perf_counter()
    records = store.find(op.entity, op.filters, limit=op.limit)
    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = op.entity
    return records, TraceStep(
        op_id=op.id,
        op_type="find",
        inputs={"entity": op.entity, "filters": [f.model_dump() for f in op.filters]},
        outputs_summary=f"{len(records)} {op.entity} record(s)",
        outputs_count=len(records),
        latency_ms=elapsed,
        target_entity=op.entity,
    )


def traverse_handler(
    op: TraverseOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[list[Record], TraceStep]:
    t0 = time.perf_counter()
    var_id = op.from_var.lstrip("$")
    source_records = ctx.bindings.get(var_id, [])
    if not isinstance(source_records, list):
        source_records = []
    rel = graph.relation(op.relation)
    via = graph.field(rel.via_field)

    targets: list[Record] = []
    if rel.cardinality in ("many_to_one", "one_to_one"):
        target_ids = [
            r[via.name]
            for r in source_records
            if isinstance(r, dict) and r.get(via.name)
        ]
        targets = store.get_many(rel.to_entity, list(dict.fromkeys(target_ids)))
    else:
        from_pk = graph.entity(rel.from_entity).primary_key
        source_ids = [
            r[from_pk]
            for r in source_records
            if isinstance(r, dict) and r.get(from_pk)
        ]
        if source_ids:
            targets = store.find(
                rel.to_entity,
                [Filter(field=via.name, operator="in", value=list(source_ids))],
                limit=1000,
            )

    if op.filters:
        targets = apply_filters(targets, op.filters, rel.to_entity, graph)

    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = rel.to_entity
    return targets, TraceStep(
        op_id=op.id,
        op_type="traverse",
        inputs={
            "from": op.from_var,
            "relation": op.relation,
            "filters": [f.model_dump() for f in op.filters],
        },
        outputs_summary=f"{len(targets)} {rel.to_entity} via {rel.verb_phrase}",
        outputs_count=len(targets),
        latency_ms=elapsed,
        graph_traversal=[op.relation],
        target_entity=rel.to_entity,
    )


def aggregate_handler(
    op: AggregateOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[Any, TraceStep]:
    t0 = time.perf_counter()
    var_id = op.source.lstrip("$")
    source = ctx.bindings.get(var_id, [])
    if not isinstance(source, list):
        source = []

    result: Any
    summary: str
    if op.operation == "count":
        result = {"count": len(source)}
        summary = f"count = {len(source)}"
    elif op.operation == "count_distinct":
        if not op.group_by_field:
            result = {"count_distinct": 0}
        else:
            distinct = {r.get(op.group_by_field) for r in source if isinstance(r, dict)}
            result = {"count_distinct": len(distinct)}
        summary = f"count_distinct = {result['count_distinct']}"
    elif op.operation in ("group_by_count", "top_n"):
        key = op.group_by_field or ""
        counts = Counter(r.get(key) for r in source if isinstance(r, dict))
        # Resolve sys_id keys to display names and value-map codes to labels so
        # the response generator never sees `grp_cloud` or `2`. The raw key is
        # preserved as `key_raw` when a translation happens, for audit/debug.
        src_var = op.source.lstrip("$")
        src_entity = ctx.var_entity.get(src_var, "")
        field = None
        if key and src_entity:
            field_id = key if "." in key else f"{src_entity}.{key}"
            if graph.has_field(field_id):
                field = graph.field(field_id)
        groups: list[dict[str, Any]] = []
        for raw_key, count in counts.most_common():
            if raw_key is None:
                continue
            display_key: Any = raw_key
            translated = False
            if field is not None:
                if field.data_type == "reference" and field.references:
                    display_key = resolver.display_name_of(
                        field.references, str(raw_key)
                    )
                    translated = True
                elif field.value_map_id is not None and isinstance(raw_key, int):
                    vm = graph.value_map(field.value_map_id)
                    display_key = vm.forward.get(raw_key, raw_key)
                    translated = raw_key in vm.forward
            entry: dict[str, Any] = {"key": display_key, "count": count}
            if translated:
                entry["key_raw"] = raw_key
            groups.append(entry)
        if op.operation == "top_n" and op.n:
            groups = groups[: op.n]
        result = {"groups": groups}
        summary = f"{len(groups)} group(s)"
    else:
        result = {"unknown": True}
        summary = "unknown aggregate operation"

    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = "_aggregate"
    return result, TraceStep(
        op_id=op.id,
        op_type="aggregate",
        inputs={"source": op.source, "operation": op.operation},
        outputs_summary=summary,
        outputs_count=len(source),
        latency_ms=elapsed,
    )


def kb_lookup_handler(
    op: KBLookupOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[list[Record], TraceStep]:
    t0 = time.perf_counter()
    if kb is None:
        records: list[Record] = []
    else:
        records = list(kb.search(op.query, category_hint=op.category_hint, top_k=op.top_k))
    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = "kb_knowledge"
    return records, TraceStep(
        op_id=op.id,
        op_type="kb_lookup",
        inputs={"query": op.query, "category_hint": op.category_hint, "top_k": op.top_k},
        outputs_summary=f"{len(records)} kb article(s)",
        outputs_count=len(records),
        latency_ms=elapsed,
        target_entity="kb_knowledge",
    )


def resolve_handler(
    op: ResolveOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[list[Record], TraceStep]:
    t0 = time.perf_counter()
    src_var = op.source.lstrip("$")
    raw = ctx.bindings.get(src_var, [])
    src_entity = ctx.var_entity.get(src_var, "")
    if not isinstance(raw, list):
        raw = []

    relations_used: list[str] = []
    warnings: list[str] = []
    rendered: list[Record] = []

    for record in raw:
        if not isinstance(record, dict):
            continue
        out: dict[str, Any] = {}
        for fname in op.fields:
            value = record.get(fname)
            if value is None:
                out[fname] = None
                continue
            field_id = f"{src_entity}.{fname}"
            if op.apply_value_maps and graph.has_field(field_id):
                field = graph.field(field_id)
                if field.value_map_id and isinstance(value, int):
                    vm = graph.value_map(field.value_map_id)
                    out[fname] = vm.forward.get(value, value)
                    continue
            out[fname] = value
        # Inline include_relations as display names.
        for rel_short in op.include_relations:
            full_rel_id = (
                f"{src_entity}.{rel_short}" if "." not in rel_short else rel_short
            )
            if not graph.has_relation(full_rel_id):
                continue
            rel = graph.relation(full_rel_id)
            relations_used.append(full_rel_id)
            via = graph.field(rel.via_field)
            ref_id = record.get(via.name)
            if not ref_id:
                continue
            display = resolver.display_name_of(rel.to_entity, str(ref_id))
            if display.startswith("[Unknown"):
                warnings.append(
                    f"dangling reference: {via.name}={ref_id} -> {display}"
                )
            out[rel_short] = display
        # Echo KB score if present
        if "_score" in record:
            out["_score"] = record["_score"]
        rendered.append(out)

    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = src_entity
    if warnings:
        ctx.warnings.extend(warnings)
    return rendered, TraceStep(
        op_id=op.id,
        op_type="resolve",
        inputs={
            "source": op.source,
            "fields": op.fields,
            "include_relations": op.include_relations,
        },
        outputs_summary=f"{len(rendered)} rendered record(s)",
        outputs_count=len(rendered),
        latency_ms=elapsed,
        warnings=warnings,
        graph_traversal=list(dict.fromkeys(relations_used)),
        target_entity=src_entity,
    )


def write_proposal_handler(
    op: WriteProposalOp,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[WriteProposal, TraceStep]:
    t0 = time.perf_counter()

    target_sys_id: str | None = None
    target_display: str = ""
    current_values: dict[str, Any] | None = None
    diff: dict[str, dict[str, Any]] = {}

    if op.target_var:
        var_id = op.target_var.lstrip("$")
        targets = ctx.bindings.get(var_id, [])
        if isinstance(targets, list) and targets:
            target = targets[0]
            target_sys_id = str(target.get("sys_id", ""))
            target_display = str(
                target.get("number") or target.get("name") or target_sys_id
            )
            current_values = dict(target)
            entity = ctx.var_entity.get(var_id, "incident")
            for fname, new_val in op.fields.items():
                old_val = target.get(fname)
                # Translate old value via value_map for human display
                old_display: Any = old_val
                if graph.has_field(f"{entity}.{fname}"):
                    field = graph.field(f"{entity}.{fname}")
                    if field.value_map_id and isinstance(old_val, int):
                        vm = graph.value_map(field.value_map_id)
                        old_display = vm.forward.get(old_val, old_val)
                diff[fname] = {"from": old_display, "to": new_val}
        else:
            ctx.warnings.append(
                f"write_proposal {op.id!r}: target_var {op.target_var} resolved to no record"
            )
    else:
        # create_incident
        target_display = str(op.fields.get("short_description", "(new incident)"))
        diff = {k: {"from": None, "to": v} for k, v in op.fields.items()}

    proposal = new_proposal(
        action=op.action,
        proposed_values=dict(op.fields),
        target_display=target_display,
        target_sys_id=target_sys_id,
        current_values=current_values,
        diff=diff,
    )

    elapsed = int((time.perf_counter() - t0) * 1000)
    ctx.var_entity[op.id] = "_write_proposal"
    return proposal, TraceStep(
        op_id=op.id,
        op_type="write_proposal",
        inputs={
            "action": op.action,
            "target_var": op.target_var,
            "fields": op.fields,
        },
        outputs_summary=f"proposal token={proposal.token}",
        outputs_count=1,
        latency_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Dispatch table — used by the engine.
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    "find": find_handler,
    "traverse": traverse_handler,
    "aggregate": aggregate_handler,
    "kb_lookup": kb_lookup_handler,
    "resolve": resolve_handler,
    "write_proposal": write_proposal_handler,
}


def dispatch(
    op: Operation,
    ctx: ExecutionContext,
    graph: SchemaGraph,
    store: DataStore,
    resolver: ReferenceResolver,
    kb: KBRetriever | None,
) -> tuple[Any, TraceStep]:
    handler = HANDLERS[op.op]
    result, step = handler(op, ctx, graph, store, resolver, kb)
    return result, step
