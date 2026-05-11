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

export interface Operation {
  op: string;
  id: string;
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
}

export interface TraceStep {
  op_id: string;
  op_type: string;
  inputs: Record<string, unknown>;
  outputs_summary: string;
  outputs_count: number;
  latency_ms: number;
  warnings: string[];
  graph_traversal: string[];
  target_entity?: string;
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

export interface SessionCreateResponse {
  session_id: string;
}

export interface ConfirmResponse {
  status: "confirmed" | "cancelled" | "expired" | "conflict" | "missing";
  record: Record<string, unknown> | null;
  message: string | null;
}
