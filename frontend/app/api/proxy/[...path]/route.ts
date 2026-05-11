/**
 * Server-side proxy: browser -> Vercel function -> Fly backend.
 *
 * Why: keeps all client traffic on `atomic-bridge.vercel.app`. The browser
 * never has to resolve `itsm-bridge-backend.fly.dev` (which can fail on
 * restrictive corporate / university DNS) — Vercel's edge network does it
 * server-side and always succeeds.
 *
 * Also: hides the backend URL from public HTML. The frontend bundle no
 * longer needs to know where the API lives.
 */

import { NextRequest } from "next/server";

const BACKEND =
  process.env.API_BASE ?? "https://itsm-bridge-backend.fly.dev";

// Stream pass-through with no caching — these are dynamic API calls.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function forward(req: NextRequest, path: string[]) {
  const target = `${BACKEND}/${path.join("/")}${req.nextUrl.search}`;

  // Strip headers the upstream doesn't need / shouldn't see.
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  const init: RequestInit = {
    method: req.method,
    headers,
    redirect: "manual",
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  const upstream = await fetch(target, init);

  // Pass through status + body. Strip hop-by-hop headers.
  const respHeaders = new Headers(upstream.headers);
  ["content-encoding", "transfer-encoding", "connection"].forEach((h) =>
    respHeaders.delete(h),
  );

  return new Response(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  });
}

export async function GET(
  req: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return forward(req, params.path);
}

export async function POST(
  req: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return forward(req, params.path);
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return forward(req, params.path);
}

export async function PUT(
  req: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return forward(req, params.path);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return forward(req, params.path);
}
