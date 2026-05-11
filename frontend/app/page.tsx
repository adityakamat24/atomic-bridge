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
  const [pendingProposal, setPendingProposal] = useState<WriteProposal | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      if (resp.write_proposal) setPendingProposal(resp.write_proposal);
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
    setPendingProposal(null);
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

  return (
    <main className="flex h-screen flex-col bg-bg">
      <header className="flex items-center justify-between border-b border-line bg-bg/90 px-4 h-14 backdrop-blur">
        <div className="flex items-center gap-3">
          <Wordmark />
          <span className="hidden h-4 w-px bg-line sm:block" />
          <SessionBadge sessionId={sessionId} />
          {trace && (
            <span className="hidden font-mono text-2xs text-fg-4 sm:inline">
              · last {trace.total_latency_ms}ms
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <a
            href={
              lastTraversed.length
                ? `/schema?highlight=${encodeURIComponent(lastTraversed.join(","))}`
                : "/schema"
            }
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-center gap-1.5 rounded-md border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-2 transition hover:border-brand-line hover:bg-brand-soft hover:text-fg"
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
            Schema graph
            {lastTraversed.length > 0 && (
              <span className="rounded border border-cta-line bg-cta-soft px-1 font-mono text-2xs text-cta">
                {lastTraversed.length}
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
          <PlanInspector plan={plan} trace={trace} />
        </aside>
      </div>

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
        <div className="mt-0.5 font-mono text-[9px] uppercase tracking-wider text-fg-4">
          ServiceNow mediation
        </div>
      </div>
    </a>
  );
}

function SessionBadge({ sessionId }: { sessionId: string | null }) {
  return (
    <span className="hidden items-center gap-1.5 sm:inline-flex">
      <span
        className={`h-1.5 w-1.5 rounded-full ${sessionId ? "bg-success" : "bg-fg-5"}`}
      />
      <span className="font-mono text-2xs text-fg-3">
        {sessionId ? `Atom · ${sessionId.slice(0, 8)}` : "no session"}
      </span>
    </span>
  );
}
