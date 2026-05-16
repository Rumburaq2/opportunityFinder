import "server-only";

import Stripe from "stripe";

let cached: Stripe | null = null;

export function getStripe(): Stripe {
  if (cached) return cached;

  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) throw new Error("Missing STRIPE_SECRET_KEY");

  cached = new Stripe(key, { apiVersion: "2026-04-22.dahlia" });
  return cached;
}
