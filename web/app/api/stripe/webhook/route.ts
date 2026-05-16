import { NextResponse, type NextRequest } from "next/server";
import type Stripe from "stripe";

import { getStripe } from "@/lib/stripe";
import { supabaseAdmin } from "@/lib/supabase/admin";

type AdminClient = ReturnType<typeof supabaseAdmin>;
type DbStatus = "none" | "active" | "past_due" | "canceled";

// Map Stripe subscription statuses → our profiles.subscription_status CHECK
// constraint (none | active | past_due | canceled). Returns null when we want
// to leave the existing row alone (e.g. incomplete = checkout in flight).
function mapStatus(s: Stripe.Subscription.Status): DbStatus | null {
  switch (s) {
    case "active":
    case "trialing":
      return "active";
    case "past_due":
    case "unpaid":
      return "past_due";
    case "canceled":
    case "incomplete_expired":
      return "canceled";
    case "incomplete":
    case "paused":
      return null;
    default:
      return null;
  }
}

export async function POST(request: NextRequest) {
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) {
    console.error("STRIPE_WEBHOOK_SECRET not configured");
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  // Raw body is required for signature verification — parsing then
  // re-stringifying breaks the byte-for-byte hash.
  const rawBody = await request.text();

  let event: Stripe.Event;
  try {
    event = getStripe().webhooks.constructEvent(rawBody, signature, secret);
  } catch (err) {
    console.error("Stripe signature verification failed:", err);
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  const admin = supabaseAdmin();

  // Idempotency: try to claim this event. Duplicates 23505 → already handled.
  const { error: insertErr } = await admin
    .from("stripe_events_seen")
    .insert({ event_id: event.id });

  if (insertErr) {
    if (insertErr.code === "23505") {
      return NextResponse.json({ ok: true, duplicate: true });
    }
    console.error("Failed to record stripe_events_seen:", insertErr);
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  try {
    await handleEvent(event, admin);
  } catch (err) {
    console.error(
      "Webhook handler failed for",
      event.id,
      event.type,
      err,
    );
    // We've already claimed the event id; without rollback, retries would
    // 23505-skip. Delete the claim so Stripe's redelivery has another shot.
    await admin.from("stripe_events_seen").delete().eq("event_id", event.id);
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}

async function handleEvent(event: Stripe.Event, admin: AdminClient): Promise<void> {
  switch (event.type) {
    case "checkout.session.completed": {
      const session = event.data.object as Stripe.Checkout.Session;
      const userId = session.client_reference_id;
      const customer =
        typeof session.customer === "string"
          ? session.customer
          : session.customer?.id ?? null;
      if (!userId || !customer) {
        console.warn(
          "checkout.session.completed missing user/customer on session",
          session.id,
        );
        return;
      }
      const { error } = await admin
        .from("profiles")
        .update({ stripe_customer_id: customer })
        .eq("id", userId);
      if (error) throw error;
      return;
    }

    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const sub = event.data.object as Stripe.Subscription;
      const userId = sub.metadata?.user_id ?? null;
      const customer =
        typeof sub.customer === "string" ? sub.customer : sub.customer.id;

      const targetStatus: DbStatus | null =
        event.type === "customer.subscription.deleted"
          ? "canceled"
          : mapStatus(sub.status);

      if (!targetStatus) {
        // incomplete / paused — don't overwrite existing state.
        return;
      }

      // In API version 2026-04-22.dahlia, current_period_end moved off the
      // Subscription onto each SubscriptionItem. With one item per sub
      // (our case), item[0] is sufficient.
      const itemEnd = sub.items?.data[0]?.current_period_end;
      const periodEnd = itemEnd ? new Date(itemEnd * 1000).toISOString() : null;

      const update = {
        subscription_status: targetStatus,
        subscription_current_period_end: periodEnd,
        stripe_customer_id: customer,
      };

      // Prefer metadata user_id (set at checkout time in 5a), fall back to
      // customer match (works for resubscribe-after-cancel paths where
      // metadata may have been stripped).
      const query = userId
        ? admin.from("profiles").update(update).eq("id", userId)
        : admin.from("profiles").update(update).eq("stripe_customer_id", customer);

      const { error } = await query;
      if (error) throw error;
      return;
    }

    default:
      // Other event types — ignored. The claim row in stripe_events_seen
      // is kept so we don't reprocess on retry.
      return;
  }
}
