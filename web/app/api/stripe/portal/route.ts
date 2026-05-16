import { NextResponse, type NextRequest } from "next/server";

import { getRequestOrigin } from "@/lib/origin";
import { createClient } from "@/lib/supabase/server";
import { getStripe } from "@/lib/stripe";

type ProfileRow = { stripe_customer_id: string | null };

export async function POST(request: NextRequest) {
  const origin = getRequestOrigin(request);
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.redirect(new URL("/login", origin), 303);
  }

  const { data: profile } = await supabase
    .from("profiles")
    .select("stripe_customer_id")
    .eq("id", user.id)
    .single<ProfileRow>();

  if (!profile?.stripe_customer_id) {
    return NextResponse.redirect(
      new URL("/account/billing?portal=no-customer", origin),
      303,
    );
  }

  const session = await getStripe().billingPortal.sessions.create({
    customer: profile.stripe_customer_id,
    return_url: `${origin}/account/billing`,
  });

  return NextResponse.redirect(session.url, 303);
}
