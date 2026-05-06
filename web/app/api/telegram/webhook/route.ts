import { NextResponse, type NextRequest } from "next/server";

import { supabaseAdmin } from "@/lib/supabase/admin";
import { sendTelegramMessage } from "@/lib/telegram";

// Telegram update payload — only the fields we read.
type TgUpdate = {
  message?: {
    chat?: { id?: number };
    text?: string;
  };
};

type LinkTokenRow = {
  user_id: string;
};

type ProfileWithChatId = {
  id: string;
};

export async function POST(request: NextRequest) {
  const expectedSecret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!expectedSecret) {
    console.error("TELEGRAM_WEBHOOK_SECRET not configured");
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  const provided = request.headers.get("x-telegram-bot-api-secret-token");
  if (provided !== expectedSecret) {
    return NextResponse.json({ ok: false }, { status: 401 });
  }

  let update: TgUpdate;
  try {
    update = (await request.json()) as TgUpdate;
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  const chatId = update.message?.chat?.id;
  const text = update.message?.text?.trim() ?? "";
  if (!chatId) {
    // Non-message update (edited_message, callback_query, etc). Ack so Telegram
    // doesn't retry, but otherwise ignore.
    return NextResponse.json({ ok: true });
  }

  const admin = supabaseAdmin();

  // /start <token> — confirm a pending link token
  if (text.startsWith("/start")) {
    const token = text.slice("/start".length).trim();
    if (!token) {
      await sendTelegramMessage(
        chatId,
        "To link an account, open Link Telegram from your account page and tap the generated link.",
      );
      return NextResponse.json({ ok: true });
    }

    const { data: row } = await admin
      .from("telegram_link_tokens")
      .select("user_id")
      .eq("token", token)
      .is("consumed_at", null)
      .gt("expires_at", new Date().toISOString())
      .maybeSingle<LinkTokenRow>();

    if (!row) {
      await sendTelegramMessage(
        chatId,
        "That link is invalid or expired. Generate a new one from your account page.",
      );
      return NextResponse.json({ ok: true });
    }

    // Drop any prior account holding this chat_id so the unique constraint holds.
    await admin
      .from("profiles")
      .update({ telegram_chat_id: null })
      .eq("telegram_chat_id", chatId)
      .neq("id", row.user_id);

    const { error: updateErr } = await admin
      .from("profiles")
      .update({ telegram_chat_id: chatId })
      .eq("id", row.user_id);

    if (updateErr) {
      console.error("Failed to set telegram_chat_id:", updateErr);
      await sendTelegramMessage(
        chatId,
        "Something went wrong linking your account. Please try again.",
      );
      return NextResponse.json({ ok: true });
    }

    await admin
      .from("telegram_link_tokens")
      .update({ consumed_at: new Date().toISOString() })
      .eq("token", token);

    await sendTelegramMessage(
      chatId,
      "Linked! You'll receive notifications here when your filters match new events.",
    );
    return NextResponse.json({ ok: true });
  }

  // /stop — unlink whichever account has this chat_id
  if (text === "/stop") {
    const { data: profile } = await admin
      .from("profiles")
      .select("id")
      .eq("telegram_chat_id", chatId)
      .maybeSingle<ProfileWithChatId>();

    if (!profile) {
      await sendTelegramMessage(
        chatId,
        "This chat isn't linked to any account.",
      );
      return NextResponse.json({ ok: true });
    }

    await admin
      .from("profiles")
      .update({ telegram_chat_id: null })
      .eq("id", profile.id);

    await sendTelegramMessage(
      chatId,
      "Unlinked. You won't receive notifications until you link again.",
    );
    return NextResponse.json({ ok: true });
  }

  // Anything else — gentle help text.
  await sendTelegramMessage(
    chatId,
    "Commands: /start <token> to link an account, /stop to unlink.",
  );
  return NextResponse.json({ ok: true });
}
