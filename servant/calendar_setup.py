"""
One-time Google Calendar authorization. Run this yourself, once:

    python -m servant.calendar_setup

This has to be a separate, manually-run step because it needs a browser to
click "Allow" -- nothing the agent does can complete an OAuth consent screen
for you, and it shouldn't be able to.

Before running it:
  1. https://console.cloud.google.com/apis/credentials -- create an OAuth
     client ID, type "Desktop app".
  2. Enable the Google Calendar API for that project.
  3. Download the client secret JSON, save it as .servant/calendar_credentials.json

Running this script opens a browser, you approve access, and it saves
.servant/calendar_token.json -- which calendar.list / calendar.create then use
on their own, refreshing silently when the token expires. Both files are
inside .servant/, which is gitignored and on the redaction forbidden-paths
list, the same protection every other credential in this project gets.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> int:
    from servant.config import load_config

    config = load_config()
    creds_path = config.path("state_dir") / "calendar_credentials.json"
    token_path = config.path("state_dir") / "calendar_token.json"

    if not creds_path.exists():
        print(f"missing {creds_path}")
        print(__doc__)
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib google-api-python-client")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    print(f"saved {token_path} -- calendar.list and calendar.create are ready to use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
