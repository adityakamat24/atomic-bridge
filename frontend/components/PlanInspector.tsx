"use client";

import { useState } from "react";
import type { ExecutionTrace, Intent, QueryPlan } from "@/lib/types";

type Tab = "plan" | "trace" | "data";

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

  if (!plan && !trace) {
    return (
      <div className="flex h-full flex-col">
        <Header active={tab} setTab={setTab} trace={null} dataCount={0} />
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="text-center">
            <div className="mb-3 inline-flex h-8 w-8 items-center justify-center rounded border border-line text-fg-4">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="7" />
                <path d="m21 21-4.3-4.3" />
              </svg>
            </div>
            <div className="text-sm text-fg-3">No query yet.</div>
            <div className="mt-1 text-2xs text-fg-4">
              The plan, execution trace, and resolved data will appear here.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <Header active={tab} setTab={setTab} trace={trace} dataCount={dataCount} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "plan" && <PlanView plan={plan} trace={trace} />}
        {tab === "trace" && <TraceView trace={trace} />}
        {tab === "data" && <DataView data={data} />}
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
}: {
  active: Tab;
  setTab: (t: Tab) => void;
  trace: ExecutionTrace | null;
  dataCount: number;
}) {
  return (
    <div className="flex items-center justify-between border-b border-line bg-bg px-4 h-10">
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
      </div>
      {trace && (
        <span className="font-mono text-2xs text-fg-4">
          {trace.total_latency_ms}ms total
        </span>
      )}
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
  _aggregate: "Aggregate",
  _write_proposal: "Proposal",
};

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
      {open && (
        <pre className="overflow-x-auto border-t border-line bg-bg p-3 font-mono text-[11px] text-fg-2 whitespace-pre-wrap break-words">
          {JSON.stringify(rest, null, 2)}
        </pre>
      )}
    </li>
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
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {s.graph_traversal.map((rel) => (
                      <span
                        key={rel}
                        className="rounded border border-cta-line bg-cta-soft px-1.5 py-px font-mono text-2xs text-brand"
                      >
                        {rel}
                      </span>
                    ))}
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
        <div className="p-6 text-sm text-fg-3">
          Query returned 0 records.
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
