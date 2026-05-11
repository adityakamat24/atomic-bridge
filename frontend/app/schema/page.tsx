"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { BrandMark } from "@/components/Logo";
import { getSchemaVisJs } from "@/lib/api";

interface NodeDetail {
  id: string;
  label?: string;
  group?: string;
  title?: string;
}

interface EdgeDetail {
  id: string;
  label?: string;
  cardinality?: string;
  title?: string;
  from?: string;
  to?: string;
}

function SchemaInner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [physicsOn, setPhysicsOn] = useState(true);
  const [stabilized, setStabilized] = useState(false);
  const [selected, setSelected] = useState<
    | { kind: "node"; data: NodeDetail }
    | { kind: "edge"; data: EdgeDetail }
    | null
  >(null);
  const params = useSearchParams();
  const highlightParam = params.get("highlight") ?? "";
  const highlighted = useMemo(
    () =>
      new Set(
        highlightParam
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      ),
    [highlightParam],
  );

  useEffect(() => {
    let destroyed = false;
    let cleanup: (() => void) | undefined;
    (async () => {
      try {
        const data = await getSchemaVisJs();
        if (destroyed) return;
        const { Network } = await import("vis-network/standalone");
        if (!containerRef.current) return;
        const network = new Network(
          containerRef.current,
          {
            nodes: data.nodes.map((n: any) => ({
              ...n,
              shape: n.group === "value_map" ? "box" : "ellipse",
              color:
                n.group === "value_map"
                  ? {
                      background: "#1c1828",
                      border: "#5d5870",
                      highlight: { background: "#261f36", border: "#8a8699" },
                    }
                  : {
                      background: "#1c1828",
                      border: "#9966FF",
                      highlight: { background: "#261f36", border: "#b794f6" },
                    },
              font: {
                color: "#f0eff5",
                size: 14,
                face: "var(--font-sans), system-ui, sans-serif",
                strokeWidth: 0,
              },
              margin: { top: 10, right: 14, bottom: 10, left: 14 },
              widthConstraint: { minimum: 90, maximum: 170 },
              borderWidth: 1.5,
              shadow: false,
            })),
            edges: data.edges.map((e: any) => {
              const isLit =
                highlighted.has(String(e.id)) || Boolean(e.highlighted);
              return {
                ...e,
                arrows: { to: { enabled: true, scaleFactor: 0.55 } },
                color: {
                  color: isLit ? "#F33F32" : "#2a2438",
                  highlight: isLit ? "#F97066" : "#5d5870",
                  hover: isLit ? "#F97066" : "#5d5870",
                },
                width: isLit ? 2.5 : 1,
                font: {
                  color: isLit ? "#F33F32" : "#5d5870",
                  size: 10,
                  face: "var(--font-mono), Menlo, monospace",
                  strokeWidth: 4,
                  strokeColor: "#0c0810",
                  align: "horizontal",
                },
                smooth: { enabled: true, type: "dynamic", roundness: 0.4 },
                length: 280,
                hoverWidth: isLit ? 3.5 : 2,
              };
            }),
          },
          {
            physics: {
              enabled: true,
              barnesHut: {
                gravitationalConstant: -14000,
                centralGravity: 0.18,
                springLength: 260,
                springConstant: 0.025,
                damping: 0.5,
                avoidOverlap: 1,
              },
              stabilization: {
                enabled: true,
                iterations: 350,
                fit: true,
              },
            },
            interaction: {
              hover: true,
              tooltipDelay: 250,
              dragNodes: true,
              zoomView: true,
              navigationButtons: false,
            },
            layout: { improvedLayout: true, randomSeed: 42 },
          },
        );

        network.once("stabilizationIterationsDone", () => {
          network.setOptions({ physics: { enabled: false } });
          setStabilized(true);
          setPhysicsOn(false);
        });

        network.on("selectNode", (params: any) => {
          const nodeId = params.nodes[0];
          const node = data.nodes.find((n: any) => n.id === nodeId);
          if (node)
            setSelected({
              kind: "node",
              data: node as unknown as NodeDetail,
            });
        });
        network.on("selectEdge", (params: any) => {
          if (params.nodes.length > 0) return;
          const edgeId = params.edges[0];
          const edge = data.edges.find((e: any) => e.id === edgeId);
          if (edge)
            setSelected({
              kind: "edge",
              data: edge as unknown as EdgeDetail,
            });
        });
        network.on("deselectNode", () => setSelected(null));
        network.on("deselectEdge", () => setSelected(null));

        networkRef.current = network;
        cleanup = () => network.destroy();
      } catch (e) {
        setError(String(e));
      }
    })();
    return () => {
      destroyed = true;
      cleanup?.();
    };
  }, [highlighted]);

  const togglePhysics = () => {
    const network = networkRef.current as any;
    if (!network) return;
    const next = !physicsOn;
    network.setOptions({ physics: { enabled: next } });
    setPhysicsOn(next);
  };

  const fit = () => {
    const network = networkRef.current as any;
    network?.fit({
      animation: { duration: 500, easingFunction: "easeInOutCubic" },
    });
  };

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
          {highlighted.size > 0 && (
            <>
              <span className="h-4 w-px bg-line" />
              <span className="inline-flex items-center gap-1.5 font-mono text-2xs text-fg-3">
                <span className="h-1.5 w-1.5 rounded-sm bg-cta" />
                {highlighted.size} relation{highlighted.size === 1 ? "" : "s"} from last query
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

      {error && (
        <div className="border-b border-danger/40 bg-danger/10 px-4 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div className="relative flex-1 min-h-0">
        <div ref={containerRef} className="absolute inset-0" />

        {/* Stabilizing indicator */}
        {!stabilized && !error && (
          <div className="absolute left-1/2 top-4 -translate-x-1/2 rounded border border-line bg-bg-1 px-2.5 py-1 font-mono text-2xs text-fg-3">
            <span className="mr-1.5 inline-block h-1 w-1 animate-pulse rounded-full bg-brand" />
            stabilizing layout
          </div>
        )}

        {/* Controls — top right */}
        <div className="absolute right-4 top-4 flex flex-col gap-1">
          <ControlButton onClick={fit} label="Fit">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 7V3h4M21 7V3h-4M3 17v4h4M21 17v4h-4" />
            </svg>
          </ControlButton>
          <ControlButton
            onClick={togglePhysics}
            label={physicsOn ? "Pin" : "Resume"}
            active={physicsOn}
          >
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${physicsOn ? "bg-success" : "bg-fg-4"}`}
            />
          </ControlButton>
        </div>

        {/* Legend — bottom left */}
        <div className="absolute bottom-4 left-4 w-64 rounded border border-line bg-bg-1/90 p-3 backdrop-blur">
          <div className="mb-2.5 font-mono text-2xs uppercase tracking-wider text-fg-4">
            Legend
          </div>
          <div className="space-y-2">
            <LegendRow
              swatch={
                <span className="block h-3 w-5 rounded-full border-2"
                  style={{ borderColor: "#9966FF", background: "#1c1828" }} />
              }
              label="Entity"
              hint="ServiceNow table"
            />
            <LegendRow
              swatch={
                <span className="block h-3 w-5 rounded-sm border-2"
                  style={{ borderColor: "#5d5870", background: "#1c1828" }} />
              }
              label="Value map"
              hint="Code ↔ display strings"
            />
            <LegendRow
              swatch={<span className="block h-px w-5 bg-fg-3" />}
              label="Relation"
              hint="Schema-level edge"
            />
            <LegendRow
              swatch={<span className="block h-[2px] w-5 bg-cta" />}
              label="Traversed"
              hint="Walked by last plan"
            />
          </div>
          <div className="mt-3 border-t border-line pt-2 font-mono text-2xs text-fg-4">
            drag · scroll to zoom · click for details
          </div>
        </div>

        {/* Selection details — bottom right */}
        {selected && (
          <div className="absolute bottom-4 right-4 max-w-sm rounded border border-line bg-bg-1/95 p-4 backdrop-blur">
            <div className="mb-1 flex items-baseline justify-between">
              <div className="font-mono text-2xs uppercase tracking-wider text-brand">
                {selected.kind === "node"
                  ? selected.data.group === "value_map"
                    ? "value map"
                    : "entity"
                  : "relation"}
              </div>
              <button
                onClick={() => setSelected(null)}
                className="text-fg-4 hover:text-fg-2"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="font-mono text-sm font-medium text-fg">
              {selected.data.label || selected.data.id}
            </div>
            <div className="mt-1 font-mono text-2xs text-fg-4">
              {selected.kind === "node"
                ? selected.data.id
                : `${(selected.data as EdgeDetail).from} → ${(selected.data as EdgeDetail).to}`}
            </div>
            {selected.data.title && (
              <p className="mt-2 text-xs leading-relaxed text-fg-2">
                {selected.data.title}
              </p>
            )}
            {selected.kind === "edge" &&
              (selected.data as EdgeDetail).cardinality && (
                <div className="mt-2 inline-flex rounded border border-line bg-bg-2 px-1.5 py-0.5 font-mono text-2xs text-fg-3">
                  {(selected.data as EdgeDetail).cardinality}
                </div>
              )}
          </div>
        )}
      </div>
    </main>
  );
}

function ControlButton({
  onClick,
  label,
  children,
  active,
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-1.5 font-mono text-2xs uppercase tracking-wider transition backdrop-blur ${
        active
          ? "border-brand-line bg-brand-soft text-brand"
          : "border-line bg-bg-1/90 text-fg-3 hover:border-line-strong hover:text-fg"
      }`}
    >
      {children}
      {label}
    </button>
  );
}

function LegendRow({
  swatch,
  label,
  hint,
}: {
  swatch: React.ReactNode;
  label: string;
  hint: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-4 w-5 items-center justify-center">{swatch}</div>
      <div className="flex-1">
        <div className="text-xs text-fg-2">{label}</div>
        <div className="font-mono text-2xs text-fg-4">{hint}</div>
      </div>
    </div>
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
