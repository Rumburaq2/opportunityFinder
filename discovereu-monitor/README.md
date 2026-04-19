# DiscoverEU Meetups Monitor

Monitors https://youth.europa.eu/discovereu/meetups_en for new meet-ups and sends a Telegram notification when changes are detected. Runs as an Azure Function App (hourly timer trigger, free Consumption plan).

---

## How It Works

1. Every hour, the function calls the DiscoverEU REST API to fetch all meetups for the current year
2. It fetches twice (30s apart) and only proceeds if both results are identical — preventing false positives from transient server responses
3. If the result differs from the last stored state, it sends a Telegram notification listing new (or removed) meetups and saves the updated state to Azure Blob Storage

---

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot`, follow the prompts, and copy the **bot token** (looks like `123456789:ABC-xyz...`)
3. Send your new bot any message (this is required before step 4)
4. Open this URL in your browser (replace `{TOKEN}` with your token):
   ```
   https://api.telegram.org/bot{TOKEN}/getUpdates
   ```
5. In the response JSON, find `result[0].message.chat.id` — that is your **chat ID**

---

### 2. Create Azure Resources (Azure Portal)

Go to [portal.azure.com](https://portal.azure.com) and follow these steps.

#### A. Resource Group
1. Search **"Resource groups"** → **Create**
2. Name: `discovereu-monitor-rg`, Region: `West Europe`
3. Click **Review + Create**

#### B. Storage Account
1. Search **"Storage accounts"** → **Create**
2. Resource group: `discovereu-monitor-rg`
3. Name: `discoeumonitor` *(must be globally unique — add random letters if taken)*
4. Region: `West Europe`, Redundancy: `LRS`
5. Click **Review + Create**
6. After creation: open the storage account → **Security + networking** → **Access keys** → copy the **Connection string** for key1

#### C. Function App
1. Search **"Function App"** → **Create**
2. Settings:
   - Resource group: `discovereu-monitor-rg`
   - Function App name: `discovereu-monitor` *(must be globally unique)*
   - Runtime stack: `Python`, Version: `3.11`
   - Region: `West Europe`
   - Hosting plan: `Consumption (Serverless)`
   - Storage account: select `discoeumonitor`
   - Operating system: `Linux`
3. Click **Review + Create**

#### D. Add Application Settings (Secrets)
1. Open the Function App → **Settings** → **Environment variables**
2. Click **+ Add** for each of the following:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from step 1 |
| `TELEGRAM_CHAT_ID` | Your chat ID from step 1 |
| `AZURE_STORAGE_CONNECTION_STRING` | Connection string from step B |

3. Click **Apply**, then **Confirm**

---

### 3. Deploy the Code

Choose one of these options — no command line required.

#### Option A — VS Code (Recommended)
1. Install the **Azure Functions** extension in VS Code
2. Click the Azure icon in the sidebar and sign in
3. Under **Workspace**, click the deploy icon (cloud with up-arrow)
4. Select **Deploy to Function App** → choose `discovereu-monitor`

#### Option B — GitHub + Deployment Center (auto-deploys on push)
1. Push this project to a GitHub repository
2. In the Azure Portal: open the Function App → **Deployment** → **Deployment Center**
3. Source: `GitHub` → authorize → select your repo and branch → **Save**
4. Azure will auto-deploy every time you push to that branch

#### Option C — Zip Upload via Kudu
1. Select all files in the project folder and zip them *(zip the files, not the folder)*
2. In the Azure Portal: open the Function App → **Development Tools** → **Advanced Tools** → **Go**
3. In Kudu: **Tools** → **Zip Push Deploy** → drag and drop the zip

---

### 4. Local Testing (Optional)

Requires [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local) and Python 3.11+.

```bash
# Install dependencies
pip install -r requirements.txt

# Set up local config
cp local.settings.json.example local.settings.json
# Edit local.settings.json and fill in your values

# Run locally
func start
```

Once running, trigger the function manually:
```
http://localhost:7071/admin/functions/check_meetups
```

You can also test without local setup by using **Code + Test** in the Azure Portal after deployment:
1. Open the Function App → **Functions** → `check_meetups` → **Code + Test**
2. Click **Test/Run** → **Run** to trigger it immediately

---

## Verifying It Works

1. After the first run, check that `meetup-state.json` was created:
   - Azure Portal → Storage account → **Storage browser** → Blob containers → `discovereu-monitor`
2. After a change is detected, you will receive a Telegram message like:

```
DiscoverEU Meetups Update!
Total: 13 -> 15 meetups

New meetups (2):
  - Malta & Ireland Meet-Up || Discover the "ISLAND LIFE"
    2026-06-17 to 2026-06-23 | MT
    https://youth.europa.eu/discovereu/meetups/malta-ireland-...

See all meetups: https://youth.europa.eu/discovereu/meetups_en
```

3. Check execution logs: Function App → **Monitor** → **Invocations**

---

## Environment Variables Reference

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Storage connection string (for state persistence) |
