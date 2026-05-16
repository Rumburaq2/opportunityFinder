# DiscoverEU Backend

Azure Function App that hourly aggregates youth-mobility opportunities from multiple sources, dedups them into Supabase, and dispatches per-user Telegram notifications based on subscription filters.

This is the **backend** half of the project. The companion **web** app (Next.js on Azure Static Web Apps, in `../web/`) handles signup, Telegram linking, and filter management. The master plan lives in `../docs/plan.md`.

---

## Architecture

A single timer-triggered function (`check_meetups`, runs every hour on the hour) does three steps in order:

1. **DiscoverEU scrape** — `scraper.py` calls the public DiscoverEU REST API (`https://youth.europa.eu/api/rest/eyp/v1/search_en`), fetches twice 30s apart to guard against transient API noise, then `events_writer.upsert_events` inserts into the Supabase `events` table.
2. **NGO adapters** — Each adapter in `adapters/` (currently `eyc_breclav` and `bfy`) fetches its source, runs new items through `llm_extractor` (Gemini) with a source-specific prompt, and upserts qualifying youth-exchange events. See `adapters/__init__.py` for the active list.
3. **Dispatcher** — `dispatcher.dispatch_pending()` calls the Supabase RPC `pending_notifications` to fetch (user, event, filter) matches, sends one Telegram message per match via `notifier.send_to_user`, then records the send in `notifications_sent` to prevent double-sending.

Each step is wrapped in try/except so a broken source can't block the others or the dispatcher.

---

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Timer entry point; orchestrates the three steps |
| `scraper.py` | DiscoverEU REST API client + content hash |
| `adapters/` | Per-NGO source adapters (RSS / HTML → LLM extraction) |
| `llm_extractor.py` | Gemini structured-output extraction shared by adapters |
| `pdf_fetcher.py` | Optional info-pack PDF download (used by `bfy`) |
| `events_writer.py` | Supabase upsert returning ids of newly inserted rows |
| `supabase_client.py` | Singleton Supabase client |
| `dispatcher.py` | Per-user fan-out: RPC → Telegram send → `notifications_sent` |
| `notifier.py` | Telegram send helper + per-event message formatter |

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key (server-only — bypasses RLS) |
| `GEMINI_API_KEY` | Google AI Studio key used by `llm_extractor` |
| `AzureWebJobsStorage` | Required by the Functions runtime itself; not read by app code |

---

## Local development

Requires Python 3.11 and [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local).

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp local.settings.json.example local.settings.json  # fill in secrets
func start
```

Trigger manually:
```bash
curl -X POST http://localhost:7071/admin/functions/check_meetups \
  -H "Content-Type: application/json" -d '{}'
```

---

## Deployment

The function runs as `discovereu-monitor-v2` (resource group `discovereu-monitor-rg`, Flex Consumption SKU, West Europe) in the `oliver-dev` Azure tenant. Deploy from the repo root:

```bash
cd backend && func azure functionapp publish discovereu-monitor-v2 --build remote
```

Manual trigger in prod:
```bash
KEY=$(az functionapp keys list -n discovereu-monitor-v2 -g discovereu-monitor-rg \
  --query masterKey -o tsv)
curl -X POST "https://discovereu-monitor-v2.azurewebsites.net/admin/functions/check_meetups" \
  -H "x-functions-key: $KEY"
```

---

## User subscription flow

End-to-end (handled by the web app, not the backend):

1. User signs up at the web app and creates one or more filters in `subscriptions_filters`.
2. User links their Telegram account at `/account/link-telegram` — the web app issues a one-time token, the user sends `/start <token>` to the bot, the web webhook stores `telegram_chat_id` on their profile.
3. On the next hourly cycle, the dispatcher's `pending_notifications` RPC matches new events against the user's filters and the backend sends one Telegram message per match.
