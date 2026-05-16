import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";
import { getStripe } from "@/lib/stripe";

type ProfileRow = { stripe_customer_id: string | null };

export async function POST(request: NextRequest) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.redirect(new URL("/login", request.url), 303);
  }

  const priceId = process.env.STRIPE_PRICE_ID;
  if (!priceId) {
    console.error("STRIPE_PRICE_ID not configured");
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  const { data: profile } = await supabase
    .from("profiles")
    .select("stripe_customer_id")
    .eq("id", user.id)
    .single<ProfileRow>();

  const origin = new URL(request.url).origin;
  const stripe = getStripe();

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    line_items: [{ price: priceId, quantity: 1 }],
    ...(profile?.stripe_customer_id
      ? { customer: profile.stripe_customer_id }
      : { customer_email: user.email ?? undefined }),
    client_reference_id: user.id,
    // Mirror user_id onto the resulting subscription so the Phase 5b webhook
    // can map customer.subscription.* events back to a profile row even when
    // client_reference_id is absent (Stripe doesn't propagate it past the
    // Checkout Session).
    subscription_data: { metadata: { user_id: user.id } },
    success_url: `${origin}/account/billing?checkout=success`,
    cancel_url: `${origin}/account/billing?checkout=cancel`,
    allow_promotion_codes: true,
  });

  if (!session.url) {
    console.error("Checkout Session has no URL:", session.id);
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  return NextResponse.redirect(session.url, 303);
}
