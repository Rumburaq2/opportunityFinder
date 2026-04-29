# DiscoverEU Meetups Monitor

Azure Function App that checks https://youth.europa.eu/discovereu/meetups_en hourly and sends a Telegram notification when new meetups are published.

## Architecture
- `function_app.py` — timer trigger (every hour), orchestrates the check
- `scraper.py` — calls the DiscoverEU REST API, normalizes and hashes meetup data
- `supabase_client.py` / `events_writer.py` — upsert meetups into Supabase `events`; `upsert_events` returns ids of newly inserted rows
- `notifier.py` — sends Telegram messages via Bot API

## How change detection works
1. Fetches meetups from the API
2. Waits 30s and fetches again — only proceeds if both results match (prevents false positives)
3. Upserts into Supabase `events`; novelty is whatever Supabase didn't already have
4. Notifies on the newly inserted rows

## API
The meetup data comes from an internal Elasticsearch REST API (not the rendered page):
`https://youth.europa.eu/api/rest/eyp/v1/search_en`
Requires `Referer` and `User-Agent` headers to avoid firewall blocking. No auth needed.

## Environment variables
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from @BotFather
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase service-role key (server-only)
- `AzureWebJobsStorage` — required by the Functions runtime itself (timer state, etc.); not used by app code

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

# Project Guidelines

## Git Workflow & Branching
You are authorized and expected to use Git commands via the terminal to manage version control. Strictly adhere to the following workflow:

1. **Never commit directly to `main` or `master`** if you are building a new feature or fixing a bug.
2. **Branching:** Before making code changes, always check the current branch. If a new feature or fix is requested, create and switch to a new branch using the format:
   - `feat/<feature-name>` for new additions.
   - `fix/<bug-name>` for bug fixes.
   - Example: `git checkout -b feat/user-authentication`

## Commit Standards (Conventional Commits)
When committing changes (`git commit`), you MUST use professional commit prefixes. The format is `<type>(<scope>): <subject>`.

**Allowed Types:**
* **feat:** A new feature (e.g., `feat: add login button`)
* **fix:** A bug fix (e.g., `fix: resolve crash on null user`)
* **docs:** Documentation only changes (e.g., `docs: update readme`)
* **style:** Changes that do not affect the meaning of the code (white-space, formatting)
* **refactor:** A code change that neither fixes a bug nor adds a feature
* **test:** Adding missing tests or correcting existing tests
* **chore:** Changes to the build process or auxiliary tools

**Commit Rules:**
* Write the commit message in the present tense imperative ("add feature" not "added feature").
* Keep the subject line concise.
* Always review `git status` and `git diff` before committing to ensure you are only staging the relevant files.
