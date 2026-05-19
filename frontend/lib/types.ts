// Mirrors the backend Pydantic types (07-api-layer.md). Kept in sync by hand;
// the smoke test in tests/integration/test_api.py asserts the response shape.

export type Intent =
  | "lookup"
  | "knowledge"
  | "analytical"
  | "cross_reference"
  | "write_proposal"
  | "ambiguous"
  | "out_of_scope";

export interface Filter {
  field: string;
  operator: string;
  value: unknown;
  case_sensitive?: boolean;
}

/**
 * Declarative traverse op as emitted by the new Planner: names a
 * target entity and the relation chain to walk; the validator confirms
 * the chain is a shortest path from the inferred source entity. The
 * legacy `relation` field is gone post-rewrite.
 */
export interface TraverseOpExtras {
  from?: string;
  to_entity?: string;
  path?: string[];
  filters_by_entity?: Record<string, Filter[]>;
}

export interface Operation {
  op: string;
  id: string;
  // Op-specific fields are loose — the inspector renders them generically
  // via JSON. TraverseOp's declarative shape is documented above.
  [key: string]: unknown;
}

export interface OutputSpec {
  format: string;
  final_var: string;
  max_items_shown: number;
}

export interface QueryPlan {
  intent: Intent;
  reasoning: string;
  operations: Operation[];
  output_spec: OutputSpec | null;
  confidence: number;
  clarification_needed: string | null;
  /**
   * When the planner returns intent=ambiguous, it MAY emit a short list
   * of disambiguated re-phrasings the user can click to re-submit. Kept
   * optional so the UI degrades gracefully when the backend doesn't
   * provide it (the clarification text alone still renders).
   */
  clarification_options?: string[];
}

export interface ScoredAlternative {
  index: number;
  path: string[];
  verb_chain: string;
  chosen: boolean;
}

export interface TraceStep {
  op_id: string;
  op_type: string;
  inputs: Record<string, unknown>;
  outputs_summary: string;
  outputs_count: number;
  latency_ms: number;
  warnings: string[];
  /**
   * For traverse steps: the relation chain that was actually walked.
   * For resolve steps: relations that were inlined via include_relations.
   * Read by the schema view to highlight edges.
   */
  graph_traversal: string[];
  target_entity?: string;
  /**
   * Entities at which filters_by_entity fired on a declarative
   * traverse. Renders as small "filtered at" badges in the trace tab.
   */
  hops_filtered?: string[];
  /**
   * Populated only when SchemaGraph.walk had to call the RelationScorer
   * to rank multiple candidate chains. Includes the ranking + the chain
   * marked `chosen` (which is the rank-0 entry).
   */
  scored_alternatives?: ScoredAlternative[] | null;
  scoring_latency_ms?: number | null;
  scoring_reasoning?: string | null;
  scoring_confidence?: number | null;
  /**
   * Every chain the engine attempted, in ranking order. When the
   * top-ranked chain returned zero records, the engine fell back to
   * the next. The entry with `used: true` is the one whose records
   * made it into the final output.
   */
  attempted_paths?: AttemptedPath[] | null;
}

export interface AttemptedPath {
  rank: number;
  candidate_index: number;
  path: string[];
  records_count: number;
  used: boolean;
}

export interface ExecutionTrace {
  request_id: string;
  plan_id: string;
  steps: TraceStep[];
  total_latency_ms: number;
}

export interface WriteProposal {
  token: string;
  action: "create_incident" | "update_incident";
  target_sys_id: string | null;
  target_display: string;
  current_values: Record<string, unknown> | null;
  proposed_values: Record<string, unknown>;
  diff: Record<string, { from: unknown; to: unknown }>;
  created_at: string;
  expires_at: string;
  nl_query: string;
  plan_id: string;
}

export interface QueryResponse {
  request_id: string;
  answer: string;
  plan: QueryPlan | null;
  trace: ExecutionTrace | null;
  data: unknown;
  intent: Intent;
  confidence: number;
  clarification_needed: string | null;
  write_proposal: WriteProposal | null;
  warnings: string[];
  latency_ms: number;
}

export type Role = "end_user" | "agent" | "manager" | "admin";

export interface SessionCreateRequest {
  role?: Role | null;
  as_user_sys_id?: string | null;
}

export interface SessionCreateResponse {
  session_id: string;
  role?: Role;
  actor_name?: string | null;
  user_sys_id?: string | null;
}

/**
 * Persona row returned by GET /v1/personas. The picker renders these
 * so you can switch to any actor in the data. The role is
 * server-derived (manages_reports > member_groups > end_user); the
 * synthetic admin row is always first.
 */
export interface PersonaSummary {
  sys_id: string | null;
  role: Role;
  name: string;
  department?: string | null;
  member_groups: string[];
  direct_report_count: number;
  managed_group_count: number;
  is_synthetic_admin: boolean;
}

export const ADMIN_FALLBACK: PersonaSummary = {
  sys_id: null,
  role: "admin",
  name: "Admin",
  department: "full access (no actor)",
  member_groups: [],
  direct_report_count: 0,
  managed_group_count: 0,
  is_synthetic_admin: true,
};

export interface ConfirmResponse {
  status: "confirmed" | "cancelled" | "expired" | "conflict" | "missing";
  record: Record<string, unknown> | null;
  message: string | null;
}
