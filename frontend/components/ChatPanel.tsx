"use client";

import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BrandMark } from "@/components/Logo";
import type { Intent, QueryResponse } from "@/lib/types";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  response?: QueryResponse;
  pending?: boolean;
  warnings?: string[];
}

const STARTERS: { tag: string; query: string }[] = [
  {
    tag: "lookup",
    query: "What's the status of John Doe's VPN issue?",
  },
  {
    tag: "cross-reference",
    query: "Show me all incidents raised by people in the Engineering department",
  },
  {
    tag: "knowledge",
    query: "How do I fix Outlook crashing?",
  },
  {
    tag: "analytical",
    query: "How many incidents does Desktop Support have right now?",
  },
];

export function ChatPanel({
  messages,
  onPickStarter,
}: {
  messages: ChatMessage[];
  onPickStarter?: (q: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0 && onPickStarter) {
    return (
      <div className="brand-bg flex flex-1 min-h-0 flex-col items-center justify-center px-8">
        <div className="w-full max-w-2xl space-y-10">
          <div className="space-y-4">
            <div className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-1/60 px-3 py-1 backdrop-blur">
              <span className="h-1.5 w-1.5 rounded-full bg-success" />
              <span className="font-mono text-2xs uppercase tracking-wider text-fg-3">
                Atom · ready
              </span>
            </div>
            <h1 className="text-4xl font-semibold tracking-tightest text-fg">
              Talk to your IT system.
              <br />
              <span className="brand-gradient-text">Atom plans, you confirm.</span>
            </h1>
            <p className="max-w-xl text-base text-fg-2 leading-relaxed">
              An agentic mediation layer over ServiceNow data. Ask in plain
              English — Atom translates, the executor runs the plan against the
              graph, and every answer ships with the trace it took to get there.
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() =>
                  document
                    .querySelector<HTMLTextAreaElement>("textarea")
                    ?.focus()
                }
                className="rounded-md bg-cta px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-cta/90"
              >
                Ask Atom
              </button>
              <a
                href="/schema"
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md border border-line bg-bg-1 px-4 py-2 text-sm text-fg-2 transition hover:border-line-strong hover:text-fg"
              >
                See the schema graph →
              </a>
            </div>
          </div>

          <div className="space-y-1">
            <div className="font-mono text-2xs uppercase tracking-wider text-fg-4">
              Try one
            </div>
            <div className="space-y-px overflow-hidden rounded-lg border border-line">
              {STARTERS.map((s) => (
                <button
                  key={s.query}
                  onClick={() => onPickStarter(s.query)}
                  className="group flex w-full items-center gap-4 bg-bg-1 px-4 py-3 text-left transition hover:bg-bg-2"
                >
                  <span className="w-28 shrink-0 font-mono text-2xs uppercase tracking-wider text-fg-4 group-hover:text-brand">
                    {s.tag}
                  </span>
                  <span className="text-sm text-fg-2 group-hover:text-fg">
                    {s.query}
                  </span>
                  <span className="ml-auto text-fg-4 opacity-0 transition group-hover:opacity-100">
                    →
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        {messages.map((m, i) => (
          <Turn key={i} message={m} isLast={i === messages.length - 1} />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Turn({ message, isLast }: { message: ChatMessage; isLast: boolean }) {
  const intent = message.response?.intent;
  const latency = message.response?.latency_ms;

  if (message.role === "system") {
    return (
      <div className="fade-in my-3 flex items-center gap-3">
        <div className="h-px flex-1 bg-line" />
        <div className="font-mono text-2xs uppercase tracking-wider text-fg-3">
          {message.content}
        </div>
        <div className="h-px flex-1 bg-line" />
      </div>
    );
  }

  const isUser = message.role === "user";

  return (
    <div className={`fade-in py-5 ${isLast ? "" : "border-b border-line"}`}>
      <div className="mb-2 flex items-center gap-2">
        {isUser ? (
          <>
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-bg-2 font-mono text-2xs font-medium text-fg-2">
              Y
            </span>
            <span className="font-mono text-2xs uppercase tracking-wider text-fg-3">
              you
            </span>
          </>
        ) : (
          <>
            <BrandMark size={20} className="rounded" />
            <span className="font-mono text-2xs uppercase tracking-wider text-fg">
              Atom
            </span>
          </>
        )}
        {!isUser && intent && <IntentTag intent={intent} />}
        {!isUser && typeof latency === "number" && (
          <span className="font-mono text-2xs text-fg-4">· {latency}ms</span>
        )}
      </div>

      <div className="pl-7 text-fg">
        {message.pending ? (
          <span className="shimmer text-sm">thinking…</span>
        ) : isUser ? (
          <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
            {message.content}
          </p>
        ) : (
          <Markdown content={message.content} />
        )}

        {message.warnings && message.warnings.length > 0 && (
          <div className="mt-3 space-y-1 border-l-2 border-warning/40 pl-3">
            {message.warnings.map((w, i) => (
              <div key={i} className="font-mono text-xs text-warning/90">
                {w}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function IntentTag({ intent }: { intent: Intent }) {
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
    <span className="flex items-center gap-1.5 font-mono text-2xs uppercase tracking-wider">
      <span className="h-1 w-1 rounded-full" style={{ background: c }} />
      <span style={{ color: c }}>{intent.replace("_", " ")}</span>
    </span>
  );
}

function Markdown({ content }: { content: string }) {
  return (
    <div className="prose-invert max-w-none text-[15px] leading-relaxed
      [&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0
      [&_strong]:font-semibold [&_strong]:text-fg
      [&_em]:italic [&_em]:text-fg-2
      [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul_li]:my-0.5
      [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_ol_li]:my-0.5
      [&_code]:rounded [&_code]:border [&_code]:border-line [&_code]:bg-bg-1 [&_code]:px-1 [&_code]:py-px [&_code]:text-[12px] [&_code]:font-mono [&_code]:text-fg
      [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-line [&_pre]:bg-bg-1 [&_pre]:p-3 [&_pre]:text-xs [&_pre]:font-mono
      [&_pre_code]:border-0 [&_pre_code]:bg-transparent [&_pre_code]:p-0
      [&_a]:text-brand [&_a]:underline [&_a]:underline-offset-2 [&_a]:decoration-brand/40 hover:[&_a]:decoration-brand
      [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-line-strong [&_blockquote]:pl-3 [&_blockquote]:text-fg-2
      [&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold
      [&_h2]:mb-1.5 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold
      [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-medium
      [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse
      [&_th]:border-b [&_th]:border-line-strong [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:text-2xs [&_th]:uppercase [&_th]:tracking-wider [&_th]:text-fg-3 [&_th]:font-mono
      [&_td]:border-b [&_td]:border-line [&_td]:px-2 [&_td]:py-1.5 [&_td]:text-xs">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
