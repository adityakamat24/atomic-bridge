"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getSchemaVisJs } from "@/lib/api";

/**
 * Reusable vis-network rendering of the schema graph.
 *
 * Used by:
 *   - the standalone /schema page (full-screen)
 *   - the inspector's Graph tab (inline, side-by-side with chat)
 *
 * Highlighted relations render red and thicker. The list of relation
 * ids comes from a prop so the parent decides what to highlight — the
 * inspector passes trace.steps.flatMap(s => s.graph_traversal); the
 * standalone page reads it from the URL.
 */
export interface SchemaGraphViewProps {
  /** Relation ids to render in the edge-highlight color. */
  highlightedRelations: string[];
  /** Entity ids the query touched (rendered with a brand-coloured border).
   *  Lights up even when no relations were walked — e.g. KB lookups touch
   *  `kb_knowledge` via FAISS without traversing any edge. */
  highlightedEntities?: string[];
  /** Compact mode hides the legend and shrinks the controls. */
  compact?: boolean;
}

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

export function SchemaGraphView({
  highlightedRelations,
  highlightedEntities = [],
  compact = false,
}: SchemaGraphViewProps) {
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

  const highlighted = useMemo(
    () => new Set(highlightedRelations.filter(Boolean)),
    [highlightedRelations],
  );
  const highlightedNodeSet = useMemo(
    () => new Set(highlightedEntities.filter(Boolean)),
    [highlightedEntities],
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
            nodes: data.nodes.map((n: any) => {
              const isTouched =
                n.group !== "value_map" &&
                highlightedNodeSet.has(String(n.id));
              return {
                ...n,
                shape: n.group === "value_map" ? "box" : "ellipse",
                borderWidth: isTouched ? 3 : 1,
                color:
                  n.group === "value_map"
                    ? {
                        background: "#1c1828",
                        border: "#5d5870",
                        highlight: {
                          background: "#261f36",
                          border: "#8a8699",
                        },
                      }
                    : isTouched
                      ? {
                          background: "#2a1f24",
                          border: "#F33F32",
                          highlight: {
                            background: "#3a2628",
                            border: "#F97066",
                          },
                        }
                      : {
                          background: "#1c1828",
                          border: "#9966FF",
                          highlight: {
                            background: "#261f36",
                            border: "#b794f6",
                          },
                        },
                font: {
                  color: isTouched ? "#F97066" : "#f0eff5",
                  size: compact ? 11 : 14,
                  face: "var(--font-sans), system-ui, sans-serif",
                  strokeWidth: 0,
                },
                margin: compact ? 8 : 14,
              };
            }),
            edges: data.edges.map((e: any) => {
              const isLit =
                highlighted.has(String(e.id)) || Boolean(e.highlighted);
              const isValueMap = e.cardinality === "value_map";
              return {
                ...e,
                arrows: { to: { enabled: !isValueMap, scaleFactor: 0.55 } },
                dashes: isValueMap ? [4, 4] : false,
                color: {
                  color: isLit
                    ? "#F33F32"
                    : isValueMap
                      ? "#3d3650"
                      : "#2a2438",
                  highlight: isLit
                    ? "#F97066"
                    : isValueMap
                      ? "#6d6685"
                      : "#5d5870",
                  hover: isLit
                    ? "#F97066"
                    : isValueMap
                      ? "#6d6685"
                      : "#5d5870",
                },
                width: isLit ? 2.5 : isValueMap ? 0.75 : 1,
                font: {
                  color: isLit
                    ? "#F33F32"
                    : isValueMap
                      ? "#7b7595"
                      : "#5d5870",
                  size: isValueMap ? 9 : 10,
                  face: "var(--font-mono), Menlo, monospace",
                  strokeWidth: 4,
                  strokeColor: "#0c0810",
                  align: "horizontal",
                },
                smooth: { enabled: true, type: "dynamic", roundness: 0.4 },
                length: isValueMap ? 180 : compact ? 200 : 280,
                hoverWidth: isLit ? 3.5 : 2,
              };
            }),
          },
          {
            physics: {
              enabled: true,
              barnesHut: {
                gravitationalConstant: compact ? -8000 : -14000,
                centralGravity: 0.18,
                springLength: compact ? 180 : 260,
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
    // Re-run when either highlight set changes so a new query re-paints
    // both edges and touched entity nodes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightedRelations.join("|"), highlightedEntities.join("|"), compact]);

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
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />

      {error && (
        <div className="absolute left-2 right-2 top-2 rounded border border-danger/40 bg-danger/10 px-3 py-1.5 text-xs text-danger">
          {error}
        </div>
      )}

      {!stabilized && !error && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded border border-line bg-bg-1 px-2 py-0.5 font-mono text-2xs text-fg-3">
          <span className="mr-1.5 inline-block h-1 w-1 animate-pulse rounded-full bg-brand" />
          stabilizing
        </div>
      )}

      {/* Controls — top right, compact */}
      <div className="absolute right-2 top-2 flex flex-col gap-1">
        <SmallControl onClick={fit} label="Fit" />
        <SmallControl
          onClick={togglePhysics}
          label={physicsOn ? "Pin" : "Run"}
          dot={physicsOn ? "var(--success)" : "var(--fg-4)"}
        />
      </div>

      {/* Highlight count badge. Splits relations + touched entities so the
          user can tell whether the query walked edges or just touched nodes
          (e.g. a KB lookup touches kb_knowledge with zero edges). */}
      {(highlighted.size > 0 || highlightedNodeSet.size > 0) && (
        <div className="absolute left-2 top-2 inline-flex items-center gap-2 rounded border border-cta-line bg-cta-soft px-2 py-0.5 font-mono text-2xs text-cta">
          {highlighted.size > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="h-px w-3 bg-cta" />
              {highlighted.size} edge{highlighted.size === 1 ? "" : "s"} walked
            </span>
          )}
          {highlighted.size > 0 && highlightedNodeSet.size > 0 && (
            <span className="text-fg-4">·</span>
          )}
          {highlightedNodeSet.size > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full border border-cta" />
              {highlightedNodeSet.size} entit
              {highlightedNodeSet.size === 1 ? "y" : "ies"} touched
            </span>
          )}
        </div>
      )}

      {/* Legend (full mode only) */}
      {!compact && (
        <div className="absolute bottom-4 left-4 w-64 rounded border border-line bg-bg-1/90 p-3 backdrop-blur">
          <div className="mb-2.5 font-mono text-2xs uppercase tracking-wider text-fg-4">
            Legend
          </div>
          <div className="space-y-2">
            <LegendRow
              swatch={
                <span
                  className="block h-3 w-5 rounded-full border-2"
                  style={{ borderColor: "#9966FF", background: "#1c1828" }}
                />
              }
              label="Entity"
              hint="ServiceNow table"
            />
            <LegendRow
              swatch={
                <span
                  className="block h-3 w-5 rounded-sm border-2"
                  style={{ borderColor: "#5d5870", background: "#1c1828" }}
                />
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
              label="Traversed edge"
              hint="Walked by last plan"
            />
            <LegendRow
              swatch={
                <span
                  className="block h-3 w-5 rounded-full border-2"
                  style={{ borderColor: "#F33F32", background: "#2a1f24" }}
                />
              }
              label="Touched entity"
              hint="Queried even with no edges (e.g. KB lookup)"
            />
          </div>
          <div className="mt-3 border-t border-line pt-2 font-mono text-2xs text-fg-4">
            drag · scroll to zoom · click for details
          </div>
        </div>
      )}

      {/* Selection details */}
      {selected && (
        <div
          className={`absolute ${compact ? "bottom-2 right-2 max-w-[260px]" : "bottom-4 right-4 max-w-sm"} rounded border border-line bg-bg-1/95 p-3 backdrop-blur`}
        >
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
  );
}

function SmallControl({
  onClick,
  label,
  dot,
}: {
  onClick: () => void;
  label: string;
  dot?: string;
}) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded border border-line bg-bg-1/90 px-1.5 py-0.5 font-mono text-2xs uppercase tracking-wider text-fg-3 transition hover:border-line-strong hover:text-fg backdrop-blur"
    >
      {dot && (
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: dot }}
        />
      )}
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
