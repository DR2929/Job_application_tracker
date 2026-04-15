# Track Agent

Reads Gmail, classifies job-related emails with Claude, and syncs application status to a Notion database. Sends a nightly digest. Runs Locally on your terminal with LaunchAgent.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

**Keys needed in `.env`:**
- `ANTHROPIC_API_KEY` — from console.anthropic.com
- `NOTION_TOKEN` — from notion.so/my-integrations
- `NOTION_DATABASE_ID` — from your Notion database URL
- `NOTION_DATASOURCE_ID` — run `python -c "import config; from notion_client import Client; n=Client(auth=config.NOTION_API_KEY); r=n.search(query='Job Applications',filter={'value':'data_source','property':'object'}); print(r['results'][0]['id'])"` to get this
- `GMAIL_CREDENTIALS_FILE` — OAuth credentials JSON from Google Cloud Console

**Gmail OAuth (first run only):**
1. Enable Gmail API at console.cloud.google.com
2. Create OAuth 2.0 Desktop credentials, download as `credentials.json`
3. Add yourself as a test user under OAuth consent screen
4. Run `python main.py once` — browser will open for one-time login

**Notion:**
1. Create a database with the schema in `plan.md` §2
2. Connect your integration to the database via `...` → Connections

## Run

```bash
python main.py once      # single pipeline run
python main.py digest    # send digest now
python main.py daemon    # poll every 30 min + nightly digest
```

## Automate (macOS)

```bash
# Poll every 30 minutes
launchctl load ~/Library/LaunchAgents/com.trackagent.plist

# Nightly digest at 7 PM
launchctl load ~/Library/LaunchAgents/com.trackagent.digest.plist
```

LaunchAgent plists are in `~/Library/LaunchAgents/`.
