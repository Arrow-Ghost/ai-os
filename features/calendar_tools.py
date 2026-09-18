"""
Google Calendar, read and write.

Needs a one-time OAuth setup that only you can do (it opens a browser and you
click Allow -- nothing the agent runs can do that for you). Setup:

    python -m servant.calendar_setup

See that module's docstring for the two-step Cloud Console part. Until you run
it, both tools below fail with the exact same instructions rather than a
confusing library error.

calendar.list is READ. calendar.create is DANGER: it can put something on
your calendar (and, if it has guests, send them an invite) that other people
may see or act on.
"""

from __future__ import annotations

import datetime as dt

from servant.sdk import Tier, ToolError, tool

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SETUP_MESSAGE = (
    "Google Calendar is not set up yet. Run `python -m servant.calendar_setup` "
    "once (see that module's docstring for the two-step Cloud Console part)."
)


def _service(ctx):
    token_path = ctx.config.path("state_dir") / "calendar_token.json"
    if not token_path.exists():
        raise ToolError(SETUP_MESSAGE)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ToolError(
            "pip install google-auth-oauthlib google-api-python-client"
        ) from exc

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    if not creds.valid:
        raise ToolError(f"the saved calendar token is invalid. {SETUP_MESSAGE}")

    return build("calendar", "v3", credentials=creds)


@tool(
    name="calendar.list",
    tier=Tier.READ,
    params={
        "days_ahead": "How many days from now to look",
        "calendar_id": "Which calendar. 'primary' is your main one.",
        "max_results": "Most events to return (1-50)",
    },
    untrusted=True,
)
def calendar_list(ctx, days_ahead: int = 7, calendar_id: str = "primary", max_results: int = 20) -> str:
    """List upcoming events. Use before proposing a new one, to spot clashes."""
    max_results = max(1, min(int(max_results), 50))
    service = _service(ctx)

    now = dt.datetime.now(dt.timezone.utc)
    until = now + dt.timedelta(days=max(1, int(days_ahead)))

    try:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(), timeMax=until.isoformat(),
            maxResults=max_results, singleEvents=True, orderBy="startTime",
        ).execute()
    except Exception as exc:  # noqa: BLE001 -- the Google client raises its own HttpError
        raise ToolError(f"could not list events: {type(exc).__name__}: {str(exc)[:200]}") from exc

    events = response.get("items", [])
    if not events:
        return f"nothing in the next {days_ahead} day(s)"

    lines = [f"{len(events)} event(s) in the next {days_ahead} day(s):"]
    for event in events:
        start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "?"))
        lines.append(f"  [{event.get('id','')[:12]}] {start}  {event.get('summary', '(no title)')}")
    return ctx.scrub("\n".join(lines))


@tool(
    name="calendar.create",
    tier=Tier.DANGER,
    params={
        "summary": "Event title",
        "start": "ISO datetime, e.g. 2026-09-20T14:00:00",
        "end": "ISO datetime. Defaults to one hour after start.",
        "description": "Event details",
        "calendar_id": "Which calendar to add it to",
    },
    undo="delete the event from Google Calendar directly, using the id printed in the result",
)
def calendar_create(
    ctx, summary: str, start: str, end: str = "", description: str = "", calendar_id: str = "primary"
) -> str:
    """Create a calendar event. Other people may see it if the calendar is shared."""
    if not summary.strip():
        raise ToolError("summary is empty")
    try:
        start_dt = dt.datetime.fromisoformat(start)
    except ValueError as exc:
        raise ToolError(f"start must be ISO format, e.g. 2026-09-20T14:00:00: {exc}") from exc
    end_dt = dt.datetime.fromisoformat(end) if end else start_dt + dt.timedelta(hours=1)

    service = _service(ctx)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }

    try:
        created = service.events().insert(calendarId=calendar_id, body=body).execute()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"could not create the event: {type(exc).__name__}: {str(exc)[:200]}") from exc

    ctx.log(f"created event {created.get('id','')}: {summary}")
    return f"created {summary!r} at {start_dt.isoformat()} (id {created.get('id','')})"
