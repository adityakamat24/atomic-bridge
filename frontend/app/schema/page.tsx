"use client";

import { Suspense, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { BrandMark } from "@/components/Logo";
import { SchemaGraphView } from "@/components/SchemaGraphView";

function SchemaInner() {
  const params = useSearchParams();
  const highlightParam = params.get("highlight") ?? "";
  const entitiesParam = params.get("entities") ?? "";
  const highlighted = useMemo(
    () =>
      highlightParam
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    [highlightParam],
  );
  const entities = useMemo(
    () =>
      entitiesParam
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    [entitiesParam],
  );

  return (
    <main className="flex h-screen flex-col bg-bg">
      <header className="flex items-center justify-between border-b border-line bg-bg px-4 h-12">
        <div className="flex items-center gap-3">
          <a href="/" className="flex items-center gap-2 text-fg">
            <BrandMark size={26} className="rounded-md" />
            <div className="leading-none">
              <div className="font-sans text-sm font-semibold tracking-tight text-fg">
                Atomic Bridge
              </div>
              <div className="mt-0.5 font-mono text-[9px] uppercase tracking-wider text-fg-4">
                Schema graph
              </div>
            </div>
          </a>
          {(highlighted.length > 0 || entities.length > 0) && (
            <>
              <span className="h-4 w-px bg-line" />
              <span className="inline-flex items-center gap-2 font-mono text-2xs text-fg-3">
                {highlighted.length > 0 && (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-sm bg-cta" />
                    {highlighted.length} edge
                    {highlighted.length === 1 ? "" : "s"}
                  </span>
                )}
                {entities.length > 0 && (
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 rounded-full border"
                      style={{ borderColor: "#F33F32" }}
                    />
                    {entities.length} entit
                    {entities.length === 1 ? "y" : "ies"}
                  </span>
                )}
                <span className="text-fg-4">from last query</span>
              </span>
            </>
          )}
        </div>
        <a
          href="/"
          className="rounded border border-transparent px-2.5 py-1 text-xs text-fg-3 transition hover:border-line hover:text-fg"
        >
          ← Chat
        </a>
      </header>

      <div className="relative flex-1 min-h-0">
        <SchemaGraphView
          highlightedRelations={highlighted}
          highlightedEntities={entities}
        />
      </div>
    </main>
  );
}

export default function SchemaPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center font-mono text-2xs text-fg-3">
          loading schema…
        </div>
      }
    >
      <SchemaInner />
    </Suspense>
  );
}
