"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BrandMark } from "@/components/Logo";
import type { Intent, QueryResponse, Role } from "@/lib/types";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  response?: QueryResponse;
  pending?: boolean;
  warnings?: string[];
  /** Structured 400 error messages, rendered as a refusal card. */
  scopeRefusal?: string[];
}

interface Starter {
  category: string;
  query: string;
  hint: string;
  icon: StarterIconName;
}

type StarterIconName =
  | "search"
  | "users"
  | "book"
  | "chart"
  | "graph"
  | "branch"
  | "tree"
  | "edit";

// Per-role starter gallery. Admin gets eight cards covering every
// backend capability; the other roles get four each, scoped to their view.
const ADMIN_STARTERS: Starter[] = [
  {
    category: "lookup",
    query: "What's the status of John Doe's VPN issue?",
    hint: "Single-record lookup with display-value resolution.",
    icon: "search",
  },
  {
    category: "cross-reference",
    query: "Show me incidents raised by people in Engineering",
    hint: "Filter by department, traverse to incidents.",
    icon: "users",
  },
  {
    category: "knowledge",
    query: "How do I fix Outlook crashing?",
    hint: "Vector search the KB, no graph walk needed.",
    icon: "book",
  },
  {
    category: "analytical",
    query: "How many incidents does Desktop Support have right now?",
    hint: "Aggregate over a team's assigned tickets.",
    icon: "chart",
  },
  {
    category: "3-hop walk",
    query: "KB articles relevant to incidents Ravi's team is handling",
    hint: "team -> incident -> category -> KB articles in one declarative step.",
    icon: "graph",
  },
  {
    category: "ambiguous",
    query: "Ravi's tickets",
    hint: "Triggers a clarification rather than guessing.",
    icon: "branch",
  },
  {
    category: "self-relation",
    query: "Who is Ravi's manager?",
    hint: "Self-loop walk on the user table.",
    icon: "tree",
  },
  {
    category: "write",
    query: "Close INC0012345",
    hint: "Proposes a write, you confirm before it lands.",
    icon: "edit",
  },
];

const END_USER_STARTERS: Starter[] = [
  {
    category: "my tickets",
    query: "Show me my open tickets",
    hint: "Only tickets where you are the caller.",
    icon: "search",
  },
  {
    category: "lookup",
    query: "What's the status of my VPN issue?",
    hint: "Scoped to your own incidents.",
    icon: "branch",
  },
  {
    category: "knowledge",
    query: "How do I fix Outlook crashing?",
    hint: "The KB is public for every role.",
    icon: "book",
  },
  {
    category: "out of scope",
    query: "Show me all users",
    hint: "Atom will refuse, because end users can't list the user table.",
    icon: "users",
  },
];

const AGENT_STARTERS: Starter[] = [
  {
    category: "my queue",
    query: "What's on my queue?",
    hint: "Tickets assigned to you OR handled by your groups.",
    icon: "search",
  },
  {
    category: "team workload",
    query: "Open incidents handled by Desktop Support",
    hint: "Filter by your group's assignment.",
    icon: "users",
  },
  {
    category: "3-hop walk",
    query: "KB articles relevant to my open incidents",
    hint: "Walks your queue to category to KB articles.",
    icon: "graph",
  },
  {
    category: "write",
    query: "Close INC0012345",
    hint: "Propose a state change on a visible ticket.",
    icon: "edit",
  },
];

const MANAGER_STARTERS: Starter[] = [
  {
    category: "my team",
    query: "Show me my team's open tickets",
    hint: "Direct reports' tickets, both caller and assignee sides.",
    icon: "users",
  },
  {
    category: "analytical",
    query: "How many tickets is my team working on?",
    hint: "Aggregate across direct reports.",
    icon: "chart",
  },
  {
    category: "priority",
    query: "Highest priority issues on my team",
    hint: "Surfaces the worst-of-list across your reports.",
    icon: "branch",
  },
  {
    category: "write",
    query: "Reassign INC0012346 to Priya Patel",
    hint: "Propose an assignee change.",
    icon: "edit",
  },
];

const STARTERS_BY_ROLE: Record<Role, Starter[]> = {
  admin: ADMIN_STARTERS,
  end_user: END_USER_STARTERS,
  agent: AGENT_STARTERS,
  manager: MANAGER_STARTERS,
};

const ROTATING_NOUNS = ["tickets", "outages", "employees", "the whole queue"];

export function ChatPanel({
  messages,
  onPickStarter,
  role = "admin",
  actorName = null,
}: {
  messages: ChatMessage[];
  onPickStarter?: (q: string) => void;
  role?: Role;
  actorName?: string | null;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0 && onPickStarter) {
    return (
      <EmptyHero
        onPickStarter={onPickStarter}
        role={role}
        actorName={actorName}
      />
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        {messages.map((m, i) => (
          <Turn
            key={i}
            message={m}
            isLast={i === messages.length - 1}
            onPickStarter={onPickStarter}
          />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

const ROLE_LABEL: Record<Role, string> = {
  admin: "admin",
  end_user: "end user",
  agent: "agent",
  manager: "manager",
};

const ROLE_HUE: Record<Role, string> = {
  admin: "var(--hue-emerald)",
  end_user: "var(--hue-slate)",
  agent: "var(--hue-cyan)",
  manager: "var(--hue-violet)",
};

function EmptyHero({
  onPickStarter,
  role,
  actorName,
}: {
  onPickStarter: (q: string) => void;
  role: Role;
  actorName: string | null;
}) {
  // The widest noun sizes the container so the rotating span doesn't reflow.
  const widest = ROTATING_NOUNS.reduce((a, b) => (b.length > a.length ? b : a));
  const starters = STARTERS_BY_ROLE[role];
  const hue = ROLE_HUE[role];
  return (
    <div className="brand-bg flex flex-1 min-h-0 flex-col items-center overflow-y-auto px-6 py-10 sm:px-8">
      <div className="my-auto w-full max-w-3xl space-y-10">
        <div className="space-y-5">
          <div className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-1/60 px-3 py-1 backdrop-blur">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            <span className="font-mono text-2xs uppercase tracking-wider text-fg-3">
              Atom · ready
            </span>
            {actorName && (
              <>
                <span className="text-fg-5">·</span>
                <span
                  className="rounded border px-1 font-mono text-2xs uppercase tracking-wider"
                  style={{ color: hue, borderColor: hue }}
                >
                  {ROLE_LABEL[role]}
                </span>
                <span className="font-mono text-2xs text-fg-3">
                  {actorName}
                </span>
              </>
            )}
          </div>
          <h1 className="text-4xl font-semibold tracking-tightest text-fg sm:text-5xl">
            Talk to your IT desk.
            <br />
            <span className="text-fg-2">Atom plans for </span>
            <span
              className="word-cycle brand-gradient-text font-semibold"
              style={{ minWidth: `${widest.length}ch` }}
              aria-live="polite"
              aria-atomic="true"
            >
              <span className="word-cycle-sizer" aria-hidden>
                {widest}
              </span>
              {ROTATING_NOUNS.map((w) => (
                <span key={w}>{w}</span>
              ))}
            </span>
            <span className="text-fg-2">.</span>
          </h1>
          <p className="max-w-2xl text-base leading-relaxed text-fg-2">
            Ask in plain English. Atom plans the walk, the engine executes
            against the graph, and you see every step it took.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() =>
                document
                  .querySelector<HTMLTextAreaElement>("textarea")
                  ?.focus()
              }
              className="rounded-md bg-cta px-5 py-2.5 text-sm font-semibold text-white shadow-[0_4px_14px_rgba(243,63,50,0.25)] transition hover:bg-cta/90 hover:shadow-[0_4px_18px_rgba(243,63,50,0.35)]"
            >
              Ask Atom
            </button>
            <a
              href="/schema"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md border border-line bg-bg-1 px-4 py-2 text-sm text-fg-2 transition hover:border-brand-line hover:bg-brand-soft hover:text-fg"
            >
              See the schema graph →
            </a>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-mono text-2xs uppercase tracking-wider text-fg-4">
              {role === "admin"
                ? "Try one of these"
                : `Try one of these as ${ROLE_LABEL[role]}`}
            </div>
            <div className="font-mono text-2xs text-fg-5">
              {starters.length} starters · click to run
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {starters.map((s) => (
              <StarterCard
                key={s.query}
                starter={s}
                onPick={() => onPickStarter(s.query)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function StarterCard({
  starter,
  onPick,
}: {
  starter: Starter;
  onPick: () => void;
}) {
  return (
    <button
      onClick={onPick}
      className="group flex items-start gap-3 rounded-lg border border-line bg-bg-1 p-4 text-left transition hover:border-brand-line hover:bg-brand-soft"
    >
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-bg-2 text-fg-3 transition group-hover:border-brand-line group-hover:text-brand">
        <StarterIcon name={starter.icon} />
      </span>
      <span className="min-w-0 flex-1 space-y-1">
        <span className="block font-mono text-2xs uppercase tracking-wider text-fg-4 group-hover:text-brand">
          {starter.category}
        </span>
        <span className="block text-sm leading-snug text-fg">
          {starter.query}
        </span>
        <span className="block text-2xs text-fg-4">{starter.hint}</span>
      </span>
      <span className="self-center text-fg-4 transition-transform group-hover:translate-x-0.5 group-hover:text-brand">
        →
      </span>
    </button>
  );
}

function StarterIcon({ name }: { name: StarterIconName }) {
  const c = {
    width: 14,
    height: 14,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "search":
      return (
        <svg {...c}>
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
      );
    case "users":
      return (
        <svg {...c}>
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      );
    case "book":
      return (
        <svg {...c}>
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
      );
    case "chart":
      return (
        <svg {...c}>
          <path d="M3 3v18h18" />
          <path d="M7 14l4-4 4 4 5-5" />
        </svg>
      );
    case "graph":
      return (
        <svg {...c}>
          <circle cx="12" cy="12" r="2" />
          <circle cx="5" cy="5" r="1.6" />
          <circle cx="19" cy="5" r="1.6" />
          <circle cx="19" cy="19" r="1.6" />
          <circle cx="5" cy="19" r="1.6" />
          <path d="m6.4 6.4 4.2 4.2M17.6 6.4l-4.2 4.2M17.6 17.6l-4.2-4.2M6.4 17.6l4.2-4.2" />
        </svg>
      );
    case "branch":
      return (
        <svg {...c}>
          <circle cx="6" cy="3" r="2" />
          <circle cx="6" cy="21" r="2" />
          <circle cx="18" cy="12" r="2" />
          <path d="M6 5v6a6 6 0 0 0 6 6h4" />
          <path d="M6 13v6" />
        </svg>
      );
    case "tree":
      return (
        <svg {...c}>
          <circle cx="12" cy="5" r="2" />
          <circle cx="6" cy="19" r="2" />
          <circle cx="18" cy="19" r="2" />
          <path d="M12 7v4M12 11l-6 6M12 11l6 6" />
        </svg>
      );
    case "edit":
      return (
        <svg {...c}>
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      );
  }
}

function Turn({
  message,
  isLast,
  onPickStarter,
}: {
  message: ChatMessage;
  isLast: boolean;
  onPickStarter?: (q: string) => void;
}) {
  const intent = message.response?.intent;
  const latency = message.response?.latency_ms;
  const plan = message.response?.plan;

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
          <ProgressStepper />
        ) : isUser ? (
          <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
            {message.content}
          </p>
        ) : message.scopeRefusal && message.scopeRefusal.length > 0 ? (
          <ScopeRefusalCard errors={message.scopeRefusal} />
        ) : (
          <Markdown content={message.content} />
        )}

        {!message.pending &&
          (intent === "ambiguous" || intent === "out_of_scope") &&
          (plan?.clarification_needed || plan?.clarification_options) && (
            <ClarificationPrompt
              text={plan?.clarification_needed ?? null}
              options={plan?.clarification_options}
              onPick={onPickStarter}
              kind={intent === "out_of_scope" ? "refusal" : "ambiguous"}
            />
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

/** Fixed-schedule three-step progress indicator (no streaming yet). */
function ProgressStepper() {
  const [stage, setStage] = useState(0);
  const [longRun, setLongRun] = useState(false);
  useEffect(() => {
    const t1 = setTimeout(() => setStage(1), 700);
    const t2 = setTimeout(() => setStage(2), 2400);
    const t3 = setTimeout(() => setLongRun(true), 8000);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, []);
  const labels = [
    "Reading your question",
    "Planning the graph walk",
    "Drafting the answer",
  ];
  return (
    <div className="space-y-1.5">
      {labels.map((label, i) => {
        const done = i < stage;
        const active = i === stage;
        return (
          <div key={label} className="flex items-center gap-2.5">
            <span
              className={`flex h-3.5 w-3.5 items-center justify-center rounded-full border ${
                done
                  ? "border-brand bg-brand"
                  : active
                  ? "border-brand"
                  : "border-fg-5"
              }`}
            >
              {done && (
                <svg
                  width="8"
                  height="8"
                  viewBox="0 0 12 12"
                  fill="none"
                  stroke="white"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M3 6.5L5 8.5L9 4" />
                </svg>
              )}
              {active && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
              )}
            </span>
            {active ? (
              <span className="shimmer text-sm">{label}</span>
            ) : (
              <span
                className={`text-sm ${done ? "text-fg-3" : "text-fg-4"}`}
              >
                {label}
              </span>
            )}
          </div>
        );
      })}
      {longRun && (
        <div className="mt-1.5 pl-6 font-mono text-2xs text-fg-4">
          Atom is on a longer walk — hang on.
        </div>
      )}
    </div>
  );
}

/** Clarification card for ambiguous / out_of_scope intents. */
function ClarificationPrompt({
  text,
  options,
  onPick,
  kind = "ambiguous",
}: {
  text: string | null;
  options?: string[];
  onPick?: (q: string) => void;
  kind?: "ambiguous" | "refusal";
}) {
  const heading =
    kind === "refusal" ? "Out of scope for this view" : "Atom needs a hint";
  const accent =
    kind === "refusal" ? "border-coral/40 bg-coral/5" : "border-amber/40 bg-amber/5";
  const accentText =
    kind === "refusal" ? "text-coral" : "text-amber";
  return (
    <div className={`mt-4 rounded-md border ${accent} p-4`}>
      <div
        className={`font-mono text-2xs uppercase tracking-wider ${accentText}`}
      >
        {heading}
      </div>
      {text && <p className="mt-2 text-sm text-fg-2">{text}</p>}
      {options && options.length > 0 && onPick && (
        <div className="mt-3 flex flex-wrap gap-2">
          {options.map((opt) => (
            <button
              key={opt}
              onClick={() => onPick(opt)}
              className="rounded-md border border-line bg-bg-1 px-3 py-1.5 text-sm text-fg-2 transition hover:border-brand-line hover:bg-brand-soft hover:text-fg"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      <div className="mt-3 font-mono text-2xs text-fg-4">
        {options && options.length > 0
          ? "Pick one above, or rephrase below."
          : kind === "refusal"
            ? "Switch persona from the header to test a different view, or rephrase."
            : "Try rephrasing below."}
      </div>
    </div>
  );
}

function ScopeRefusalCard({ errors }: { errors: string[] }) {
  return (
    <div className="rounded-md border border-coral/40 bg-coral/5 p-4">
      <div className="font-mono text-2xs uppercase tracking-wider text-coral">
        Plan rejected by guardrails
      </div>
      <p className="mt-2 text-sm text-fg-2">
        The plan Atom drafted violates the current view scope. The validator
        caught it before any data was read.
      </p>
      <ul className="mt-2 space-y-1">
        {errors.map((e, i) => (
          <li key={i} className="flex items-start gap-2 font-mono text-2xs text-fg-2">
            <span className="text-coral">·</span>
            <span className="break-words">{e}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 font-mono text-2xs text-fg-4">
        Switch persona from the header to test a different view, or rephrase
        below.
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
