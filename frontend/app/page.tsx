"use client";

import { useEffect, useState } from "react";
import { ApprovalDialog } from "@/components/ApprovalDialog";
import { ChatMessage, ChatPanel } from "@/components/ChatPanel";
import { BrandMark } from "@/components/Logo";
import { MessageInput } from "@/components/MessageInput";
import { PlanInspector } from "@/components/PlanInspector";
import {
  ApiError,
  confirmWrite,
  createSession,
  submitQuery,
} from "@/lib/api";
import type {
  ExecutionTrace,
  QueryPlan,
  WriteProposal,
} from "@/lib/types";

export default function HomePage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [plan, setPlan] = useState<QueryPlan | null>(null);
  const [trace, setTrace] = useState<ExecutionTrace | null>(null);
  const [data, setData] = useState<unknown>(null);
  const [pendingProposal, setPendingProposal] = useState<WriteProposal | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  useEffect(() => {
    createSession()
      .then((s) => setSessionId(s.session_id))
      .catch(() => setSessionId(null));
  }, []);

  const append = (m: ChatMessage) => setMessages((prev) => [...prev, m]);
  const replaceLastWith = (m: ChatMessage) =>
    setMessages((prev) => [...prev.slice(0, -1), m]);

  const send = async (q: string) => {
    setError(null);
    append({ role: "user", content: q });
    append({ role: "assistant", content: "", pending: true });
    setBusy(true);
    try {
      const resp = await submitQuery(q, sessionId, true, true);
      replaceLastWith({
        role: "assistant",
        content: resp.answer,
        response: resp,
        warnings: resp.warnings,
      });
      setPlan(resp.plan);
      setTrace(resp.trace);
      setData(resp.data);
      if (resp.write_proposal) setPendingProposal(resp.write_proposal);
      // Mobile-only: surface the new plan/trace automatically.
      if (
        typeof window !== "undefined" &&
        window.matchMedia("(max-width: 1023px)").matches
      ) {
        setInspectorOpen(true);
      }
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
      replaceLastWith({ role: "system", content: `error: ${msg}` });
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!pendingProposal) return;
    try {
      const out = await confirmWrite(pendingProposal.token, true);
      const num =
        out.record && typeof out.record === "object" && "number" in out.record
          ? String((out.record as Record<string, unknown>).number)
          : "";
      append({
        role: "system",
        content: num ? `confirmed · ${num} updated` : "confirmed",
      });
    } catch (e) {
      append({ role: "system", content: `confirm failed: ${String(e)}` });
    } finally {
      setPendingProposal(null);
    }
  };

  const handleCancelProposal = async () => {
    if (!pendingProposal) return;
    try {
      await confirmWrite(pendingProposal.token, false);
      append({ role: "system", content: "cancelled" });
    } catch (e) {
      append({ role: "system", content: `cancel failed: ${String(e)}` });
    } finally {
      setPendingProposal(null);
    }
  };

  const newSession = async () => {
    setMessages([]);
    setPlan(null);
    setTrace(null);
    setData(null);
    setPendingProposal(null);
    setInspectorOpen(false);
    try {
      const s = await createSession();
      setSessionId(s.session_id);
    } catch {
      setSessionId(null);
    }
  };

  const lastTraversed = trace?.steps
    ? Array.from(new Set(trace.steps.flatMap((s) => s.graph_traversal)))
    : [];
  const lastTouchedEntities = trace?.steps
    ? Array.from(
        new Set(
          trace.steps
            .map((s) => s.target_entity ?? "")
            .filter((e) => e && !e.startsWith("_")),
        ),
      )
    : [];
  const schemaHref = (() => {
    const qs = new URLSearchParams();
    if (lastTraversed.length)
      qs.set("highlight", lastTraversed.join(","));
    if (lastTouchedEntities.length)
      qs.set("entities", lastTouchedEntities.join(","));
    const s = qs.toString();
    return s ? `/schema?${s}` : "/schema";
  })();

  const hasInspectable = plan != null || trace != null;

  return (
    <main className="flex h-screen flex-col bg-bg">
      <header className="flex items-center justify-between border-b border-line bg-bg/90 px-4 h-14 backdrop-blur">
        <div className="flex items-center gap-3">
          <Wordmark />
          <span className="hidden h-4 w-px bg-line sm:block" />
          <AtomStatusChip busy={busy} sessionId={sessionId} />
          {trace && (
            <span className="hidden font-mono text-2xs text-fg-4 sm:inline">
              · last {trace.total_latency_ms}ms
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <a
            href={schemaHref}
            target="_blank"
            rel="noopener noreferrer"
            title="Open the schema graph full-screen in a new tab. The Graph tab on the right shows the same view inline."
            className="group hidden items-center gap-1.5 rounded-md border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-2 transition hover:border-brand-line hover:bg-brand-soft hover:text-fg sm:flex"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="2.5" />
              <circle cx="6" cy="6" r="1.8" />
              <circle cx="18" cy="6" r="1.8" />
              <circle cx="6" cy="18" r="1.8" />
              <circle cx="18" cy="18" r="1.8" />
              <path d="m9 10 3 1.5L15 10" />
              <path d="M12 13.5V18" />
              <path d="m9 10-3-2" />
              <path d="m15 10 3-2" />
            </svg>
            Full-screen graph
            {lastTraversed.length + lastTouchedEntities.length > 0 && (
              <span className="rounded border border-cta-line bg-cta-soft px-1 font-mono text-2xs text-cta">
                {lastTraversed.length}e · {lastTouchedEntities.length}n
              </span>
            )}
          </a>
          <button
            onClick={newSession}
            className="rounded-md border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-2 transition hover:border-line-strong hover:text-fg"
          >
            New session
          </button>
        </div>
      </header>

      <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,3fr)_minmax(420px,2fr)]">
        <section className="flex min-h-0 flex-col border-r border-line">
          <ChatPanel messages={messages} onPickStarter={send} />
          <MessageInput onSubmit={send} disabled={busy} />
        </section>
        <aside className="hidden min-h-0 overflow-hidden bg-bg lg:block">
          <PlanInspector plan={plan} trace={trace} data={data} />
        </aside>
      </div>

      {/* Mobile / tablet only: a floating button + bottom sheet for the
          inspector, since the aside is hidden below the lg breakpoint. */}
      {hasInspectable && !inspectorOpen && (
        <button
          onClick={() => setInspectorOpen(true)}
          aria-label="Open inspector"
          className="lg:hidden fixed bottom-24 right-4 z-30 flex h-12 items-center gap-2 rounded-full border border-brand-line bg-bg-2/90 px-4 text-xs text-fg shadow-[0_6px_20px_rgba(0,0,0,0.45)] backdrop-blur transition hover:border-brand"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
          <span className="font-mono text-2xs uppercase tracking-wider">
            Inspect
          </span>
          {trace && trace.steps.length > 0 && (
            <span className="rounded border border-cta-line bg-cta-soft px-1 font-mono text-2xs text-cta">
              {trace.steps.length}
            </span>
          )}
        </button>
      )}

      {inspectorOpen && (
        <MobileInspectorSheet
          onClose={() => setInspectorOpen(false)}
        >
          <PlanInspector plan={plan} trace={trace} data={data} />
        </MobileInspectorSheet>
      )}

      {pendingProposal && (
        <ApprovalDialog
          proposal={pendingProposal}
          onConfirm={handleConfirm}
          onCancel={handleCancelProposal}
        />
      )}
      {error && (
        <div className="fade-in fixed bottom-4 right-4 max-w-md rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger backdrop-blur">
          {error}
        </div>
      )}
    </main>
  );
}

function Wordmark() {
  return (
    <a href="/" className="flex items-center gap-2 text-fg">
      <BrandMark size={26} className="rounded-md" />
      <div className="leading-none">
        <div className="font-sans text-sm font-semibold tracking-tight text-fg">
          Atomic Bridge
        </div>
        <div className="mt-0.5 hidden font-mono text-[9px] uppercase tracking-wider text-fg-4 sm:block">
          ServiceNow mediation
        </div>
      </div>
    </a>
  );
}

function AtomStatusChip({
  busy,
  sessionId,
}: {
  busy: boolean;
  sessionId: string | null;
}) {
  // Replaces the silent session-only badge with one that also reflects
  // whether Atom is currently planning a response. Echoes atomicwork.com's
  // small "Atom" agent badges.
  const ready = !!sessionId && !busy;
  const noSession = !sessionId;
  let label: string;
  let dotClass: string;
  if (busy) {
    label = "Atom · planning";
    dotClass = "bg-amber pulse-dot";
  } else if (noSession) {
    label = "no session";
    dotClass = "bg-fg-5";
  } else {
    label = `Atom · ready${sessionId ? ` · ${sessionId.slice(0, 8)}` : ""}`;
    dotClass = "bg-success";
  }
  return (
    <span className="hidden items-center gap-1.5 sm:inline-flex">
      <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
      <span className="font-mono text-2xs text-fg-3">{label}</span>
    </span>
  );
}

function MobileInspectorSheet({
  onClose,
  children,
}: {
  onClose: () => void;
  children: React.ReactNode;
}) {
  // Slide-up sheet for the inspector on screens below the lg breakpoint.
  // Esc closes, backdrop click closes, and the close button is the first
  // focusable element so screen-readers land there on open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Prevent body scroll while open.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);
  return (
    <div className="lg:hidden fixed inset-0 z-40">
      <button
        aria-label="Close inspector"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <div
        role="dialog"
        aria-modal="true"
        className="absolute inset-x-0 bottom-0 flex h-[78vh] flex-col rounded-t-xl border border-line bg-bg shadow-[0_-12px_40px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="h-1 w-8 rounded-full bg-fg-5" />
            <span className="font-mono text-2xs uppercase tracking-wider text-fg-3">
              Inspector
            </span>
          </div>
          <button
            onClick={onClose}
            autoFocus
            className="flex h-7 w-7 items-center justify-center rounded-md border border-line text-fg-3 transition hover:border-line-strong hover:text-fg"
            aria-label="Close"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    </div>
  );
}
