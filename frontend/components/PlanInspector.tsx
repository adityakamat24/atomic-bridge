"use client";

import { useMemo, useState } from "react";
import type { ExecutionTrace, Intent, QueryPlan } from "@/lib/types";
import { SchemaGraphView } from "@/components/SchemaGraphView";

type Tab = "plan" | "trace" | "data" | "graph";

export function PlanInspector({
  plan,
  trace,
  data,
}: {
  plan: QueryPlan | null;
  trace: ExecutionTrace | null;
  data?: unknown;
}) {
  const [tab, setTab] = useState<Tab>("plan");
  const dataCount = countData(data);
  const traversed = useMemo(
    () =>
      trace
        ? Array.from(new Set(trace.steps.flatMap((s) => s.graph_traversal)))
        : [],
    [trace],
  );
  // Entities the query TOUCHED, even when no edges were walked.
  // A `kb_lookup` op touches kb_knowledge via FAISS without traversing;
  // a pure `find` touches one entity without traversing; etc. The graph
  // view highlights these nodes so every query lights up something.
  // We skip synthetic "_aggregate" / "_write_proposal" entities — those
  // aren't graph entities.
  const touchedEntities = useMemo(
    () =>
      trace
        ? Array.from(
            new Set(
              trace.steps
                .map((s) => s.target_entity ?? "")
                .filter((e) => e && !e.startsWith("_")),
            ),
          )
        : [],
    [trace],
  );

  const hasContent = plan != null || trace != null;

  return (
    <div className="flex h-full flex-col">
      <Header
        active={tab}
        setTab={setTab}
        trace={trace}
        dataCount={dataCount}
        traversedCount={traversed.length}
      />
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === "graph" ? (
          // The graph tab is always renderable — pre-query it shows the
          // full schema for exploration; after a query it highlights the
          // chain that was walked AND the entity nodes the query touched.
          // The dual highlighting lets KB lookups (which walk zero edges)
          // still light up the `kb_knowledge` node.
          <div className="h-full">
            <SchemaGraphView
              highlightedRelations={traversed}
              highlightedEntities={touchedEntities}
              compact
            />
          </div>
        ) : !hasContent ? (
          <EmptyState />
        ) : tab === "plan" ? (
          <div className="h-full overflow-y-auto">
            <PlanView plan={plan} trace={trace} />
          </div>
        ) : tab === "trace" ? (
          <div className="h-full overflow-y-auto">
            <TraceView trace={trace} />
          </div>
        ) : (
          <div className="h-full overflow-y-auto">
            <DataView data={data} />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  // Teaching empty-state: tell the user what each tab will show *before*
  // the first query, so the inspector doesn't feel like dead real-estate.
  const rows: { tag: string; title: string; body: string }[] = [
    {
      tag: "plan",
      title: "How Atom will answer",
      body: "The structured walk Atom is about to run — intent, operations, output shape.",
    },
    {
      tag: "trace",
      title: "What Atom actually did",
      body: "Every op, every record count, every fallback chain the engine tried.",
    },
    {
      tag: "data",
      title: "The resolved records",
      body: "Display-value rows ready to read — no sys_id soup.",
    },
    {
      tag: "graph",
      title: "Schema map (browseable now)",
      body: "After a query, the walked chain lights up in red. Switch tabs to explore.",
    },
  ];
  return (
    <div className="brand-bg flex h-full flex-col items-center justify-center px-6 py-10">
      <div className="w-full max-w-sm space-y-4">
        <div className="text-center">
          <div className="font-mono text-2xs uppercase tracking-wider text-fg-4">
            Inspector
          </div>
          <div className="mt-1 text-sm text-fg-2">
            Ask Atom anything — the plan, trace, and data land here.
          </div>
        </div>
        <ol className="space-y-1.5">
          {rows.map((r, i) => (
            <li
              key={r.tag}
              className="flex items-start gap-3 rounded-md border border-line bg-bg-1/70 px-3 py-2"
            >
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border border-line bg-bg-2 font-mono text-2xs text-fg-3">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                  <span className="font-mono text-2xs uppercase tracking-wider text-brand">
                    {r.tag}
                  </span>
                  <span className="text-xs text-fg-2">{r.title}</span>
                </span>
                <span className="mt-0.5 block text-2xs text-fg-4">
                  {r.body}
                </span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function countData(data: unknown): number {
  if (data === null || data === undefined) return 0;
  if (Array.isArray(data)) return data.length;
  if (typeof data === "object") {
    const groups = (data as Record<string, unknown>).groups;
    if (Array.isArray(groups)) return groups.length;
    return 1;
  }
  return 1;
}

function Header({
  active,
  setTab,
  trace,
  dataCount,
  traversedCount,
}: {
  active: Tab;
  setTab: (t: Tab) => void;
  trace: ExecutionTrace | null;
  dataCount: number;
  traversedCount: number;
}) {
  const subtitleFor: Record<Tab, string> = {
    plan: "how Atom will answer",
    trace: "what Atom actually did",
    data: "resolved records",
    graph: "schema map",
  };
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line bg-bg px-4 h-10">
      <div className="flex items-center gap-1">
        <TabButton on={active === "plan"} onClick={() => setTab("plan")}>
          Plan
        </TabButton>
        <TabButton on={active === "trace"} onClick={() => setTab("trace")}>
          Trace
          {trace && trace.steps.length > 0 && (
            <span className="ml-1 font-mono text-2xs text-fg-4">
              {trace.steps.length}
            </span>
          )}
        </TabButton>
        <TabButton on={active === "data"} onClick={() => setTab("data")}>
          Data
          {dataCount > 0 && (
            <span className="ml-1 font-mono text-2xs text-fg-4">
              {dataCount}
            </span>
          )}
        </TabButton>
        <TabButton on={active === "graph"} onClick={() => setTab("graph")}>
          Graph
          {traversedCount > 0 && (
            <span className="ml-1 font-mono text-2xs text-cta">
              {traversedCount}
            </span>
          )}
        </TabButton>
      </div>
      <div className="flex min-w-0 items-center gap-3">
        <span className="hidden truncate text-2xs italic text-fg-4 md:inline">
          {subtitleFor[active]}
        </span>
        {trace && (
          <span className="shrink-0 font-mono text-2xs text-fg-4">
            {trace.total_latency_ms}ms total
          </span>
        )}
      </div>
    </div>
  );
}

function TabButton({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`relative h-10 px-3 text-2xs font-medium uppercase tracking-wider transition ${
        on ? "text-fg" : "text-fg-3 hover:text-fg-2"
      }`}
    >
      {children}
      {on && <span className="absolute inset-x-0 -bottom-px h-px bg-brand" />}
    </button>
  );
}

function PlanView({
  plan,
  trace,
}: {
  plan: QueryPlan | null;
  trace: ExecutionTrace | null;
}) {
  if (!plan)
    return (
      <div className="p-6 text-sm text-fg-3">
        No plan was generated for this turn.
      </div>
    );
  const entityWalk = deriveEntityWalk(trace);
  return (
    <div className="space-y-6 px-4 py-5">
      <Field label="Intent">
        <div className="flex items-baseline gap-3">
          <IntentChip intent={plan.intent} />
          <span className="font-mono text-2xs text-fg-4">
            confidence {(plan.confidence * 100).toFixed(0)}%
          </span>
        </div>
      </Field>

      {entityWalk.length > 0 && (
        <Field label="Graph path">
          <EntityWalk entities={entityWalk} />
        </Field>
      )}

      <Field label="Reasoning">
        <p className="text-sm leading-relaxed text-fg-2">{plan.reasoning}</p>
      </Field>

      <Field label={`Operations (${plan.operations.length})`}>
        <ol className="space-y-2">
          {plan.operations.map((op, i) => (
            <OpRow key={op.id} op={op} index={i + 1} />
          ))}
        </ol>
      </Field>

      {plan.output_spec && (
        <Field label="Output">
          <div className="font-mono text-xs text-fg-2">
            {plan.output_spec.format}{" "}
            <span className="text-fg-4">→</span>{" "}
            <span className="text-brand">${plan.output_spec.final_var}</span>
          </div>
        </Field>
      )}

      {plan.clarification_needed && (
        <Field label="Clarification needed">
          <div className="text-sm text-fg-2 italic">
            {plan.clarification_needed}
          </div>
        </Field>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 font-mono text-2xs uppercase tracking-wider text-fg-4">
        {label}
      </div>
      {children}
    </div>
  );
}

function deriveEntityWalk(trace: ExecutionTrace | null): string[] {
  if (!trace) return [];
  const walk: string[] = [];
  for (const step of trace.steps) {
    const e = step.target_entity;
    if (!e) continue;
    if (walk.length === 0 || walk[walk.length - 1] !== e) {
      walk.push(e);
    }
  }
  return walk;
}

const ENTITY_LABEL: Record<string, string> = {
  incident: "Incident",
  sys_user: "User",
  sys_user_group: "Team",
  kb_knowledge: "KB Article",
  category: "Category",
  _aggregate: "Aggregate",
  _write_proposal: "Proposal",
};

function entityLabel(e: string): string {
  return ENTITY_LABEL[e] ?? e;
}

function EntityWalk({ entities }: { entities: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {entities.map((e, i) => (
        <span key={`${e}-${i}`} className="flex items-center gap-1.5">
          {i > 0 && (
            <span className="font-mono text-2xs text-fg-4" aria-hidden>
              →
            </span>
          )}
          <span
            className="rounded border border-cta-line bg-cta-soft px-2 py-0.5 font-mono text-2xs text-brand"
            title={e}
          >
            {ENTITY_LABEL[e] ?? e}
          </span>
        </span>
      ))}
    </div>
  );
}

const OP_GLYPHS: Record<string, string> = {
  find: "F",
  traverse: "T",
  aggregate: "Σ",
  kb_lookup: "K",
  resolve: "R",
  write_proposal: "W",
};

const OP_HUE: Record<string, string> = {
  find: "var(--hue-cyan)",
  traverse: "var(--hue-blue)",
  aggregate: "var(--hue-violet)",
  kb_lookup: "var(--hue-emerald)",
  resolve: "var(--fg-3)",
  write_proposal: "var(--hue-orange)",
};

function OpRow({ op, index }: { op: Record<string, unknown>; index: number }) {
  const opName = String(op.op);
  const opId = String(op.id);
  const { op: _, id: __, ...rest } = op;
  const [open, setOpen] = useState(false);
  const hue = OP_HUE[opName] ?? "var(--fg-3)";
  return (
    <li className="rounded-md border border-line bg-bg-1">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition hover:bg-bg-2"
      >
        <span className="w-4 text-right font-mono text-2xs text-fg-4">{index}</span>
        <span
          className="flex h-5 w-5 items-center justify-center rounded border font-mono text-2xs font-medium"
          style={{ borderColor: hue, color: hue }}
        >
          {OP_GLYPHS[opName] ?? "?"}
        </span>
        <span className="font-mono text-xs text-fg">{opName}</span>
        <span className="font-mono text-2xs text-fg-4">#{opId}</span>
        <span className="ml-auto text-fg-4">
          {open ? "−" : "+"}
        </span>
      </button>
      <OpSummary op={op} />
      {open && (
        <pre className="overflow-x-auto border-t border-line bg-bg p-3 font-mono text-[11px] text-fg-2 whitespace-pre-wrap break-words">
          {JSON.stringify(rest, null, 2)}
        </pre>
      )}
    </li>
  );
}

/**
 * Always-visible compact summary of each op's intent. Makes the plan
 * readable at a glance without expanding the JSON dump:
 *
 *   find #u                 sys_user where name contains "Ravi"
 *   traverse #kb            $u → raised incidents → in category → has articles → kb_knowledge
 *                            filtered at incident: state in [New, In Progress, On Hold]
 *   resolve #out            number, short_description, state · via reportedBy
 */
function OpSummary({ op }: { op: Record<string, unknown> }) {
  const opName = String(op.op);
  switch (opName) {
    case "find":
      return <FindSummary op={op} />;
    case "traverse":
      return <TraverseSummary op={op} />;
    case "aggregate":
      return <AggregateSummary op={op} />;
    case "kb_lookup":
      return <KBLookupSummary op={op} />;
    case "resolve":
      return <ResolveSummary op={op} />;
    case "write_proposal":
      return <WriteProposalSummary op={op} />;
    default:
      return null;
  }
}

function FindSummary({ op }: { op: Record<string, unknown> }) {
  const entity = String(op.entity ?? "");
  const filters = (op.filters as unknown[] | undefined) ?? [];
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <EntityChip entity={entity} />
        {filters.length > 0 && (
          <>
            <span className="text-fg-4">where</span>
            <FilterChips filters={filters} />
          </>
        )}
      </div>
    </div>
  );
}

function TraverseSummary({ op }: { op: Record<string, unknown> }) {
  const fromVar = String(op.from ?? "");
  const toEntity = String(op.to_entity ?? "");
  const path = (op.path as string[] | undefined) ?? [];
  const fbe = (op.filters_by_entity as
    | Record<string, unknown[]>
    | undefined) ?? {};
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <VarChip name={fromVar} />
        {path.length > 0 ? (
          path.map((relId) => (
            <span key={relId} className="flex items-center gap-1.5">
              <span className="text-fg-4">→</span>
              <RelationChip relId={relId} />
            </span>
          ))
        ) : (
          <span className="text-fg-4">→ (reflexive)</span>
        )}
        {toEntity && (
          <>
            <span className="text-fg-4">→</span>
            <EntityChip entity={toEntity} />
          </>
        )}
      </div>
      {Object.keys(fbe).length > 0 && (
        <div className="mt-1.5 space-y-1">
          {Object.entries(fbe).map(([ent, filters]) => (
            <div key={ent} className="flex flex-wrap items-center gap-1.5">
              <span className="text-2xs text-fg-4">
                filtered at <EntityChipMini entity={ent} />:
              </span>
              <FilterChips filters={filters as unknown[]} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AggregateSummary({ op }: { op: Record<string, unknown> }) {
  const source = String(op.source ?? "");
  const operation = String(op.operation ?? "");
  const groupBy = op.group_by_field as string | undefined;
  const n = op.n as number | undefined;
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5 text-fg-2">
        <VarChip name={source} />
        <span className="text-fg-4">→</span>
        <span className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs">
          {operation}
        </span>
        {groupBy && (
          <>
            <span className="text-fg-4">by</span>
            <span className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs">
              {groupBy}
            </span>
          </>
        )}
        {n != null && (
          <span className="text-2xs text-fg-4">top {n}</span>
        )}
      </div>
    </div>
  );
}

function KBLookupSummary({ op }: { op: Record<string, unknown> }) {
  const q = String(op.query ?? "");
  const hint = op.category_hint as string | undefined;
  const topK = op.top_k as number | undefined;
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-2xs text-fg-2">&ldquo;{q}&rdquo;</span>
        {hint && (
          <span className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-3">
            category={hint}
          </span>
        )}
        {topK != null && (
          <span className="text-2xs text-fg-4">top {topK}</span>
        )}
      </div>
    </div>
  );
}

function ResolveSummary({ op }: { op: Record<string, unknown> }) {
  const source = String(op.source ?? "");
  const fields = (op.fields as string[] | undefined) ?? [];
  const rels = (op.include_relations as string[] | undefined) ?? [];
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <VarChip name={source} />
        <span className="text-fg-4">→</span>
        <span className="text-fg-3">fields:</span>
        {fields.map((f) => (
          <span
            key={f}
            className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-2"
          >
            {f}
          </span>
        ))}
      </div>
      {rels.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-2xs text-fg-4">via:</span>
          {rels.map((r) => (
            <span
              key={r}
              className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-brand"
            >
              {r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function WriteProposalSummary({ op }: { op: Record<string, unknown> }) {
  const action = String(op.action ?? "");
  const targetVar = op.target_var as string | undefined;
  const fields = (op.fields as Record<string, unknown> | undefined) ?? {};
  return (
    <div className="border-t border-line/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-cta">
          {action}
        </span>
        {targetVar && (
          <>
            <span className="text-fg-4">on</span>
            <VarChip name={targetVar} />
          </>
        )}
      </div>
      {Object.keys(fields).length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-2xs text-fg-4">set:</span>
          {Object.entries(fields).map(([k, v]) => (
            <span
              key={k}
              className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-2"
            >
              {k}={String(v)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Reusable chips -------------------------------------------------------

function EntityChip({ entity }: { entity: string }) {
  if (!entity) return null;
  return (
    <span
      className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-brand"
      title={entity}
    >
      {entityLabel(entity)}
    </span>
  );
}

function EntityChipMini({ entity }: { entity: string }) {
  return (
    <span
      className="ml-0.5 rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-2"
      title={entity}
    >
      {entityLabel(entity)}
    </span>
  );
}

function RelationChip({ relId }: { relId: string }) {
  // relId is like "sys_user.incidentsReported" — split for cleaner display.
  const dot = relId.indexOf(".");
  const verb = dot >= 0 ? relId.slice(dot + 1) : relId;
  return (
    <span
      className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-brand"
      title={relId}
    >
      {verb}
    </span>
  );
}

function VarChip({ name }: { name: string }) {
  return (
    <span className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-2">
      {name}
    </span>
  );
}

function FilterChips({ filters }: { filters: unknown[] }) {
  return (
    <>
      {filters.map((f, i) => {
        if (!f || typeof f !== "object") return null;
        const fr = f as Record<string, unknown>;
        const field = String(fr.field ?? "");
        const op = String(fr.operator ?? "");
        const v = fr.value;
        const valueStr = Array.isArray(v)
          ? `[${v.join(", ")}]`
          : v == null
          ? ""
          : String(v);
        return (
          <span
            key={i}
            className="rounded border border-line bg-bg px-1.5 py-px font-mono text-2xs text-fg-2"
          >
            {field} {op} {valueStr}
          </span>
        );
      })}
    </>
  );
}

function TraceView({ trace }: { trace: ExecutionTrace | null }) {
  if (!trace || trace.steps.length === 0) {
    return (
      <div className="p-6 text-sm text-fg-3">
        No execution steps (terminal intent like ambiguous or out_of_scope).
      </div>
    );
  }
  const total = Math.max(1, trace.total_latency_ms);
  return (
    <div className="px-4 py-5">
      <ol className="relative space-y-1">
        {trace.steps.map((s, i) => {
          const pct = Math.min(100, (s.latency_ms / total) * 100);
          const hue = OP_HUE[s.op_type] ?? "var(--fg-3)";
          return (
            <li key={s.op_id} className="relative pl-8">
              {/* timeline rail */}
              {i < trace.steps.length - 1 && (
                <span className="absolute left-3 top-7 h-full w-px bg-line" />
              )}
              <span
                className="absolute left-1.5 top-2 flex h-5 w-5 items-center justify-center rounded border font-mono text-2xs font-medium"
                style={{ borderColor: hue, color: hue, background: "var(--bg-0)" }}
              >
                {OP_GLYPHS[s.op_type] ?? "?"}
              </span>
              <div className="rounded-md border border-line bg-bg-1 px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-fg">{s.op_type}</span>
                  <span className="font-mono text-2xs text-fg-4">#{s.op_id}</span>
                  <span className="ml-auto font-mono text-2xs text-fg-3">
                    {s.latency_ms}ms
                  </span>
                </div>
                {/* latency bar */}
                <div className="mt-1.5 h-px overflow-hidden rounded bg-bg-3">
                  <div
                    className="h-full"
                    style={{ width: `${pct}%`, background: hue }}
                  />
                </div>
                <div className="mt-2 text-xs text-fg-2">{s.outputs_summary}</div>
                {s.graph_traversal.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1">
                    {s.graph_traversal.map((rel, idx) => (
                      <span key={rel} className="flex items-center gap-1">
                        {idx > 0 && (
                          <span className="text-2xs text-fg-3">→</span>
                        )}
                        <span className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-brand">
                          {rel}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
                {s.hops_filtered && s.hops_filtered.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    <span className="text-2xs text-fg-3">filtered at:</span>
                    {s.hops_filtered.map((entity) => (
                      <span
                        key={entity}
                        className="rounded border border-line/60 bg-bg-2 px-1.5 py-px font-mono text-2xs text-fg-2"
                      >
                        {entity}
                      </span>
                    ))}
                  </div>
                )}
                {s.scored_alternatives && s.scored_alternatives.length > 0 && (
                  <div className="mt-2 rounded border border-line/60 bg-bg-2/60 p-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-2xs uppercase tracking-wider text-cta">
                        LLM scorer ranked {s.scored_alternatives.length} paths
                      </span>
                      {s.scoring_latency_ms != null && (
                        <span className="font-mono text-2xs text-fg-4">
                          +{s.scoring_latency_ms}ms
                        </span>
                      )}
                      {s.scoring_confidence != null && (
                        <span
                          className={`font-mono text-2xs ${
                            s.scoring_confidence < 0.4
                              ? "text-warning"
                              : "text-fg-3"
                          }`}
                        >
                          conf {(s.scoring_confidence * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                    <ul className="mt-1.5 space-y-0.5">
                      {s.scored_alternatives.map((alt) => (
                        <li
                          key={alt.index}
                          className={`flex items-center gap-1.5 ${
                            alt.chosen ? "text-fg" : "text-fg-3"
                          }`}
                        >
                          <span className="w-3 text-2xs">
                            {alt.chosen ? "✓" : "·"}
                          </span>
                          <span className="font-mono text-2xs">
                            {alt.verb_chain}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {s.scoring_reasoning && (
                      <div className="mt-1.5 border-t border-line/60 pt-1 text-2xs italic text-fg-3">
                        “{s.scoring_reasoning}”
                      </div>
                    )}
                  </div>
                )}
                {s.attempted_paths && s.attempted_paths.length > 1 && (
                  // When the engine fell back through multiple chains,
                  // surface the ranking so the user sees which
                  // interpretations were tried and which produced the
                  // answer.
                  <div className="mt-2 rounded border border-warning/30 bg-warning/5 p-2">
                    <div className="font-mono text-2xs uppercase tracking-wider text-warning">
                      Engine fallback · walked {s.attempted_paths.length} chains
                    </div>
                    <ul className="mt-1.5 space-y-0.5">
                      {s.attempted_paths.map((att) => (
                        <li
                          key={att.rank}
                          className={`flex items-center gap-1.5 font-mono text-2xs ${
                            att.used ? "text-fg" : "text-fg-4"
                          }`}
                        >
                          <span className="w-3 text-right">{att.rank}.</span>
                          <span>{att.path.join(" → ") || "(reflexive)"}</span>
                          <span className="ml-auto">
                            {att.records_count} rec
                            {att.records_count === 1 ? "" : "s"}
                          </span>
                          <span className="w-3">
                            {att.used ? "✓" : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {s.warnings.length > 0 && (
                  <div className="mt-1.5 space-y-0.5 border-l border-warning/40 pl-2">
                    {s.warnings.map((w, j) => (
                      <div key={j} className="font-mono text-2xs text-warning/90">
                        {w}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function DataView({ data }: { data: unknown }) {
  if (data === null || data === undefined) {
    return (
      <div className="p-6 text-sm text-fg-3">
        No structured data was returned for this turn.
      </div>
    );
  }

  // List of records → table
  if (Array.isArray(data)) {
    if (data.length === 0) {
      return (
        <div className="px-4 py-6">
          <div className="rounded-md border border-line bg-bg-1 p-4">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full border border-line text-fg-4">
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <path d="M12 8v4M12 16h.01" />
                </svg>
              </span>
              <span className="text-sm text-fg-2">
                This walk returned 0 records.
              </span>
            </div>
            <div className="mt-2 pl-7 text-2xs text-fg-4">
              The trace tab shows every chain the engine tried — useful for
              spotting filter mismatches or empty branches.
            </div>
          </div>
        </div>
      );
    }
    const isRecordList = data.every(
      (r) => r !== null && typeof r === "object" && !Array.isArray(r),
    );
    if (isRecordList) {
      const rows = data as Record<string, unknown>[];
      const cols = unionKeys(rows);
      return (
        <div className="px-4 py-5">
          <div className="mb-2 font-mono text-2xs uppercase tracking-wider text-fg-4">
            {rows.length} record{rows.length === 1 ? "" : "s"}
          </div>
          <div className="overflow-x-auto rounded-md border border-line bg-bg-1">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-line bg-bg-2/40">
                  {cols.map((c) => (
                    <th
                      key={c}
                      className="px-2 py-1.5 text-left font-mono text-2xs uppercase tracking-wider text-fg-3"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr
                    key={i}
                    className={i < rows.length - 1 ? "border-b border-line" : ""}
                  >
                    {cols.map((c) => (
                      <td
                        key={c}
                        className="px-2 py-1.5 align-top font-mono text-2xs text-fg-2"
                      >
                        {renderCell(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }
    // Scalar list — render as ordered list
    return (
      <div className="px-4 py-5">
        <ol className="space-y-1">
          {data.map((v, i) => (
            <li key={i} className="font-mono text-xs text-fg-2">
              {String(v)}
            </li>
          ))}
        </ol>
      </div>
    );
  }

  // Object (aggregate result like {"count": 2} or {"groups": [...]})
  if (typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (Array.isArray(obj.groups)) {
      return <DataView data={obj.groups} />;
    }
    return (
      <div className="px-4 py-5">
        <dl className="space-y-2">
          {Object.entries(obj).map(([k, v]) => (
            <div
              key={k}
              className="flex items-baseline gap-3 rounded-md border border-line bg-bg-1 px-3 py-2"
            >
              <dt className="font-mono text-2xs uppercase tracking-wider text-fg-4">
                {k}
              </dt>
              <dd className="font-mono text-xs text-fg">{renderCell(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  }

  // Scalar
  return (
    <div className="p-6">
      <div className="font-mono text-sm text-fg">{String(data)}</div>
    </div>
  );
}

function unionKeys(rows: Record<string, unknown>[]): string[] {
  const seen: string[] = [];
  const set = new Set<string>();
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (k.startsWith("_")) continue;
      if (!set.has(k)) {
        set.add(k);
        seen.push(k);
      }
    }
  }
  return seen;
}

function renderCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function IntentChip({ intent }: { intent: Intent }) {
  const colors: Record<Intent, string> = {
    lookup: "var(--hue-cyan)",
    knowledge: "var(--hue-emerald)",
    analytical: "var(--hue-violet)",
    cross_reference: "var(--hue-blue)",
    write_proposal: "var(--hue-orange)",
    ambiguous: "var(--fg-3)",
    out_of_scope: "var(--hue-rose)",
  };
  const c = colors[intent] ?? "var(--fg-3)";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-1 w-1 rounded-full" style={{ background: c }} />
      <span className="font-mono text-xs uppercase tracking-wider" style={{ color: c }}>
        {intent.replace("_", " ")}
      </span>
    </span>
  );
}
