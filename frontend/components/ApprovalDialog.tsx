"use client";

import { useEffect, useState } from "react";
import type { WriteProposal } from "@/lib/types";

export function ApprovalDialog({
  proposal,
  onConfirm,
  onCancel,
}: {
  proposal: WriteProposal;
  onConfirm: () => Promise<void>;
  onCancel: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [remainingMs, setRemainingMs] = useState(
    Math.max(0, new Date(proposal.expires_at).getTime() - Date.now()),
  );

  useEffect(() => {
    const t = setInterval(() => {
      setRemainingMs(
        Math.max(0, new Date(proposal.expires_at).getTime() - Date.now()),
      );
    }, 1000);
    return () => clearInterval(t);
  }, [proposal.expires_at]);

  const handle = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  const minutes = Math.floor(remainingMs / 60000);
  const seconds = Math.floor((remainingMs % 60000) / 1000);
  const isUpdate = proposal.action === "update_incident";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm fade-in">
      <div className="w-full max-w-xl overflow-hidden rounded-lg border border-line-strong bg-bg-1 shadow-2xl">
        <div className="border-b border-line px-5 py-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div className="font-mono text-2xs uppercase tracking-wider text-cta">
                pending {isUpdate ? "update" : "create"}
              </div>
              <div className="mt-1 text-base font-medium text-fg">
                {isUpdate
                  ? `Update ${proposal.target_display}`
                  : `Create incident: ${proposal.target_display}`}
              </div>
            </div>
            <div className="text-right">
              <div className="font-mono text-2xs uppercase tracking-wider text-fg-4">
                expires
              </div>
              <div className="mt-1 font-mono text-sm tabular-nums text-fg-2">
                {minutes}:{seconds.toString().padStart(2, "0")}
              </div>
            </div>
          </div>
          {proposal.nl_query && (
            <div className="mt-2 font-mono text-2xs text-fg-4">
              from: <span className="text-fg-3">{proposal.nl_query}</span>
            </div>
          )}
        </div>

        <div className="px-5 py-4">
          <div className="mb-2 font-mono text-2xs uppercase tracking-wider text-fg-4">
            Diff
          </div>
          <div className="overflow-hidden rounded border border-line">
            {Object.entries(proposal.diff).map(([field, change], i, arr) => (
              <div
                key={field}
                className={`grid grid-cols-[120px_1fr_20px_1fr] items-center gap-3 bg-bg px-3 py-2 ${
                  i < arr.length - 1 ? "border-b border-line" : ""
                }`}
              >
                <div className="font-mono text-xs text-fg-3">{field}</div>
                <div className="truncate font-mono text-xs text-danger/80 line-through">
                  {String(change.from ?? "—")}
                </div>
                <div className="text-center text-fg-4">→</div>
                <div className="truncate font-mono text-xs text-success">
                  {String(change.to)}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line bg-bg px-5 py-3">
          <button
            disabled={busy}
            onClick={() => handle(onCancel)}
            className="rounded border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-2 transition hover:border-line-strong hover:text-fg disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            disabled={busy}
            onClick={() => handle(onConfirm)}
            className="rounded-md bg-cta px-3.5 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-cta/90 disabled:opacity-40"
          >
            {busy ? "Applying…" : "Confirm write"}
          </button>
        </div>
      </div>
    </div>
  );
}
