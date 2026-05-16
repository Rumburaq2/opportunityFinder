import type { NextRequest } from "next/server";

type HeadersLike = { get(name: string): string | null };

// Azure SWA (and most reverse proxies) terminate TLS at the edge and proxy
// to the Next runtime over an internal hop, so request.url reflects the
// proxy's origin (e.g. https://localhost:8080), not the public hostname the
// user typed. Read the forwarded headers when present.
export function originFromHeaders(headers: HeadersLike): string | null {
  const host = headers.get("x-forwarded-host");
  if (!host) return null;
  const proto = headers.get("x-forwarded-proto") ?? "https";
  return `${proto}://${host}`;
}

export function getRequestOrigin(request: NextRequest): string {
  return originFromHeaders(request.headers) ?? new URL(request.url).origin;
}
