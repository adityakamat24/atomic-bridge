"use client";

import { useState } from "react";
import type { ExecutionTrace, Intent, QueryPlan } from "@/lib/types";

type Tab = "plan" | "trace";

export function PlanInspector({
  plan,
  trace,
}: {
  plan: QueryPlan | null;
  trace: ExecutionTrace | null;
}) {
  const [tab, setTab] = useState<Tab>("plan");

  if (!plan && !trace) {
    return (
      <div className="flex h-full flex-col">
        <Header active={tab} setTab={setTab} trace={null} />
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
              The plan and execution trace will appear here.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <Header active={tab} setTab={setTab} trace={trace} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "plan" && <PlanView plan={plan} />}
        {tab === "trace" && <TraceView trace={trace} />}
      </div>
    </div>
  );
}

function Header({
  active,
  setTab,
  trace,
}: {
  active: Tab;
  setTab: (t: Tab) => void;
  trace: ExecutionTrace | null;
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

function PlanView({ plan }: { plan: QueryPlan | null }) {
  if (!plan)
    return (
      <div className="p-6 text-sm text-fg-3">
        No plan was generated for this turn.
      </div>
    );
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
