import type {
  ConfirmResponse,
  QueryResponse,
  SessionCreateResponse,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export async function submitQuery(
  query: string,
  sessionId: string | null,
  showPlan = true,
  showTrace = true,
): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE}/v1/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      session_id: sessionId,
      show_plan: showPlan,
      show_trace: showTrace,
    }),
  });
  return jsonOrThrow<QueryResponse>(res);
}

export async function createSession(): Promise<SessionCreateResponse> {
  const res = await fetch(`${API_BASE}/v1/session`, { method: "POST" });
  return jsonOrThrow<SessionCreateResponse>(res);
}

export async function confirmWrite(
  token: string,
  confirm: boolean,
): Promise<ConfirmResponse> {
  const res = await fetch(`${API_BASE}/v1/write/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, confirm }),
  });
  return jsonOrThrow<ConfirmResponse>(res);
}

export async function getSchemaVisJs(): Promise<{
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
}> {
  const res = await fetch(`${API_BASE}/v1/schema/visjs`);
  return jsonOrThrow(res);
}
