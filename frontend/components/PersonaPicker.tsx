"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PersonaSummary, Role } from "@/lib/types";

const ROLE_HUE: Record<Role, string> = {
  admin: "var(--hue-emerald)",
  end_user: "var(--hue-slate)",
  agent: "var(--hue-cyan)",
  manager: "var(--hue-violet)",
};

const ROLE_LABEL: Record<Role, string> = {
  admin: "admin",
  end_user: "end user",
  agent: "agent",
  manager: "manager",
};

const ROLE_ORDER: Record<Role, number> = {
  admin: 0,
  manager: 1,
  agent: 2,
  end_user: 3,
};

export function PersonaPicker({
  personas,
  current,
  onChange,
  loading = false,
}: {
  personas: PersonaSummary[];
  current: PersonaSummary;
  onChange: (p: PersonaSummary) => void;
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClickAway = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickAway);
    document.addEventListener("keydown", onKey);
    setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onClickAway);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const ranked = [...personas].sort((a, b) => {
      const ra = ROLE_ORDER[a.role];
      const rb = ROLE_ORDER[b.role];
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name);
    });
    if (!q) return ranked;
    return ranked.filter((p) => {
      const hay = `${p.name} ${p.department ?? ""} ${
        p.member_groups.join(" ")
      } ${ROLE_LABEL[p.role]}`.toLowerCase();
      return hay.includes(q);
    });
  }, [personas, query]);

  const hue = ROLE_HUE[current.role];

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-md border border-line bg-bg-1 px-2.5 py-1.5 text-xs transition hover:border-line-strong"
      >
        <span
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-2xs font-medium text-bg"
          style={{ background: hue }}
          aria-hidden
        >
          {initials(current.name)}
        </span>
        <span className="flex flex-col items-start leading-tight">
          <span className="font-mono text-2xs uppercase tracking-wider text-fg-4">
            View as
          </span>
          <span className="text-fg">{current.name}</span>
        </span>
        <span
          className="ml-1 rounded border px-1 font-mono text-2xs uppercase tracking-wider"
          style={{ color: hue, borderColor: hue }}
        >
          {ROLE_LABEL[current.role]}
        </span>
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`text-fg-4 transition ${open ? "rotate-180" : ""}`}
          aria-hidden
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div
          role="listbox"
          className="fade-in absolute right-0 top-full z-40 mt-1.5 w-[22rem] overflow-hidden rounded-lg border border-line bg-bg-1 shadow-[0_18px_48px_rgba(0,0,0,0.55)]"
        >
          <div className="border-b border-line px-3 py-2">
            <div className="font-mono text-2xs uppercase tracking-wider text-fg-4">
              Switch persona
            </div>
            <input
              ref={inputRef}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, department, group…"
              className="mt-1.5 w-full rounded border border-line bg-bg-2 px-2 py-1 text-sm text-fg outline-none placeholder:text-fg-4 focus:border-line-strong"
            />
          </div>
          {loading && personas.length === 0 ? (
            <div className="px-3 py-6 text-center font-mono text-2xs text-fg-4">
              Loading personas…
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-3 py-6 text-center font-mono text-2xs text-fg-4">
              No users match {JSON.stringify(query)}.
            </div>
          ) : (
            <ul className="max-h-[60vh] divide-y divide-line/70 overflow-y-auto">
              {filtered.map((p) => {
                const phue = ROLE_HUE[p.role];
                const isCurrent =
                  p.sys_id === current.sys_id && p.role === current.role;
                return (
                  <li key={`${p.role}:${p.sys_id ?? "_admin"}`}>
                    <button
                      role="option"
                      aria-selected={isCurrent}
                      onClick={() => {
                        onChange(p);
                        setOpen(false);
                        setQuery("");
                      }}
                      className={`flex w-full items-start gap-3 px-3 py-2.5 text-left transition ${
                        isCurrent ? "bg-bg-2" : "hover:bg-bg-2"
                      }`}
                    >
                      <span
                        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-xs font-semibold text-bg"
                        style={{ background: phue }}
                        aria-hidden
                      >
                        {initials(p.name)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm text-fg">
                            {p.name}
                          </span>
                          <span
                            className="rounded border px-1 font-mono text-2xs uppercase tracking-wider"
                            style={{ color: phue, borderColor: phue }}
                          >
                            {ROLE_LABEL[p.role]}
                          </span>
                        </span>
                        {p.department && (
                          <span className="block text-2xs text-fg-4">
                            {p.department}
                          </span>
                        )}
                        <span className="mt-1 flex flex-wrap items-center gap-1 text-2xs text-fg-3">
                          {p.direct_report_count > 0 && (
                            <span>
                              {p.direct_report_count} direct report
                              {p.direct_report_count === 1 ? "" : "s"}
                            </span>
                          )}
                          {p.managed_group_count > 0 && (
                            <span>
                              · manages {p.managed_group_count} team
                              {p.managed_group_count === 1 ? "" : "s"}
                            </span>
                          )}
                          {p.member_groups.length > 0 && (
                            <span className="truncate">
                              {p.direct_report_count + p.managed_group_count >
                              0
                                ? "· "
                                : ""}
                              in {p.member_groups.join(", ")}
                            </span>
                          )}
                          {p.direct_report_count +
                            p.managed_group_count +
                            p.member_groups.length ===
                            0 &&
                            !p.is_synthetic_admin && (
                              <span className="text-fg-4">
                                no groups, no reports
                              </span>
                            )}
                        </span>
                      </span>
                      {isCurrent && (
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke={phue}
                          strokeWidth="2.2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="mt-1.5 shrink-0"
                          aria-hidden
                        >
                          <path d="M5 12l5 5L20 7" />
                        </svg>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="border-t border-line bg-bg/60 px-3 py-2 text-2xs text-fg-4">
            Role auto-derived from the user's data. Switching persona starts a
            fresh session.
          </div>
        </div>
      )}
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}
