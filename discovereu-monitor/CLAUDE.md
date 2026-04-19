# DiscoverEU Meetups Monitor

Azure Function App that checks https://youth.europa.eu/discovereu/meetups_en hourly and sends a Telegram notification when new meetups are published.

## Architecture
- `function_app.py` — timer trigger (every hour), orchestrates the check
- `scraper.py` — calls the DiscoverEU REST API, normalizes and hashes meetup data
- `state.py` — reads/writes `meetup-state.json` to Azure Blob Storage
- `notifier.py` — sends Telegram messages via Bot API

## How change detection works
1. Fetches meetups from the API
2. Waits 30s and fetches again — only proceeds if both results match (prevents false positives)
3. Compares hash against stored state in blob storage
4. Notifies and saves new state only if a real change is confirmed

## API
The meetup data comes from an internal Elasticsearch REST API (not the rendered page):
`https://youth.europa.eu/api/rest/eyp/v1/search_en`
Requires `Referer` and `User-Agent` headers to avoid firewall blocking. No auth needed.

## Environment variables
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from @BotFather
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `AZURE_STORAGE_CONNECTION_STRING` — Azure Storage connection string (also set as `AzureWebJobsStorage` in local.settings.json)

## Local development
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp local.settings.json.example local.settings.json  # fill in secrets
func start
# trigger manually:
curl -X POST http://localhost:7071/admin/functions/check_meetups -H "Content-Type: application/json" -d '{}'
```

## Deployment
```bash
az login
source .venv/bin/activate
func azure functionapp publish discovereu-monitor
```
