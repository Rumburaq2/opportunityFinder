import type { NextRequest } from "next/server";

// Azure SWA (and most reverse proxies) terminate TLS at the edge and proxy
// to the Next runtime over an internal hop, so request.url reflects the
// proxy's origin (e.g. https://localhost:8080), not the public hostname the
// user typed. Prefer the forwarded headers when present; fall back to the
// request URL for local `next dev`, where no proxy is in front.
export function getRequestOrigin(request: NextRequest): string {
  const host = request.headers.get("x-forwarded-host");
  if (host) {
    const proto = request.headers.get("x-forwarded-proto") ?? "https";
    return `${proto}://${host}`;
  }
  return new URL(request.url).origin;
}
