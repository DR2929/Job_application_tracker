import os
from dotenv import load_dotenv

load_dotenv()

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-5"
CLASSIFIER_MAX_TOKENS = 1024

# Notion
NOTION_API_KEY = os.getenv("NOTION_TOKEN", os.getenv("NOTION_API_KEY", ""))
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")      # for page creation
NOTION_DATASOURCE_ID = os.getenv("NOTION_DATASOURCE_ID", "")  # for querying (v3 API)

# Gmail
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "token.json")
GMAIL_POLL_INTERVAL_MINUTES = 30
DIGEST_RECIPIENT = os.getenv("DIGEST_RECIPIENT", "")  # configure when ready
DIGEST_SEND_TIME = "21:00"  # local time

# Behavior
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"  # default ON
MIN_CONFIDENCE_THRESHOLD = 0.5
STATE_FILE = "state.json"
FAILED_QUEUE_FILE = "failed_queue.json"
LOG_FILE = "tracker.log"
SKIPPED_LOG_FILE = "skipped.log"
