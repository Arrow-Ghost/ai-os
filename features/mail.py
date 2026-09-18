"""
Gmail over IMAP/SMTP, using an app password.

Why not the Gmail API: the API needs an OAuth consent flow, a Cloud project
and a browser round-trip. An app password is a 16-character string that works
with the standard library and nothing else. Set up at
https://myaccount.google.com/apppasswords, revoked from the same page.

WHAT AN APP PASSWORD IS. Full, permanent mailbox access -- read AND send --
that bypasses two-factor auth. It is not scoped and it does not expire. Treat
it exactly like the account password:
  * it lives in .env, which is gitignored and on the agent's forbidden-paths
    list, so no tool can read it back
  * it is in the redaction patterns, so it cannot reach the model
  * revoke it the moment you are done demoing

TIERS. Reading is READ. Drafting is WRITE -- a draft sits in your Drafts
folder and nobody sees it. Sending is DANGER and stays DANGER: another human
receives it and you cannot take it back.
"""

from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage

from servant.sdk import Tier, ToolError, tool

IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
MAX_LIMIT = 50


def _credentials(ctx) -> tuple[str, str]:
    address = (ctx.secret("GMAIL_ADDRESS") or "").strip()
    password = (ctx.secret("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not address:
        raise ToolError(
            "GMAIL_ADDRESS is not set in .env -- put the full address of the mailbox "
            "the app password belongs to, e.g. GMAIL_ADDRESS=you@gmail.com"
        )
    if not password:
        raise ToolError(
            "GMAIL_APP_PASSWORD is not set in .env. Make one at "
            "https://myaccount.google.com/apppasswords (16 characters, spaces optional)."
        )
    if len(password) != 16:
        raise ToolError(
            f"GMAIL_APP_PASSWORD should be 16 characters once spaces are removed, got "
            f"{len(password)}. This is an app password, not your account password."
        )
    return address, password


def _connect(ctx) -> imaplib.IMAP4_SSL:
    address, password = _credentials(ctx)
    try:
        box = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
        box.login(address, password)
    except imaplib.IMAP4.error as exc:
        raise ToolError(
            f"Gmail refused the login for {address}. Check that the app password belongs "
            f"to this exact address and has not been revoked. ({str(exc)[:120]})"
        ) from exc
    except OSError as exc:
        raise ToolError(f"could not reach {IMAP_HOST}: {exc}") from exc
    return box


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 -- malformed headers are common in the wild
        return value


def _body(message) -> str:
    """Prefer text/plain; fall back to stripping the HTML part."""
    if not message.is_multipart():
        payload = message.get_payload(decode=True) or b""
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace")

    html = ""
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            return text
        if part.get_content_type() == "text/html" and not html:
            html = text

    if html:
        import re

        return re.sub(r"<[^>]+>", " ", html)
    return ""


# --------------------------------------------------------------------------
# READ
# --------------------------------------------------------------------------

@tool(
    name="mail.list",
    tier=Tier.READ,
    params={
        "folder": "Mailbox to look in, e.g. INBOX",
        "limit": "How many of the most recent messages to return (1-50)",
        "unread_only": "Only messages you have not read",
        "search": "IMAP search term matching subject or sender, e.g. 'github'",
    },
)
def mail_list(ctx, folder: str = "INBOX", limit: int = 10, unread_only: bool = False, search: str = "") -> str:
    """List recent emails: id, date, sender and subject. Use before mail.read."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    box = _connect(ctx)
    try:
        status, _ = box.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise ToolError(f"no such mailbox: {folder}")

        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        if search:
            criteria = ["OR", "SUBJECT", f'"{search}"', "FROM", f'"{search}"'] + (
                ["UNSEEN"] if unread_only else []
            )

        status, data = box.search(None, *criteria)
        if status != "OK":
            raise ToolError(f"search failed in {folder}")

        ids = (data[0] or b"").split()[-limit:]
        if not ids:
            return f"{folder}: nothing matches"

        lines = [f"{folder}: {len(ids)} message(s), newest last"]
        for message_id in ids:
            status, raw = box.fetch(
                message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            if status != "OK" or not raw or not raw[0]:
                continue
            header = email.message_from_bytes(raw[0][1])
            lines.append(
                f"  [{message_id.decode()}] {_decode(header.get('Date'))[:31]}\n"
                f"       from: {_decode(header.get('From'))[:70]}\n"
                f"       subj: {_decode(header.get('Subject'))[:70]}"
            )
        return ctx.scrub("\n".join(lines))
    finally:
        _logout(box)


@tool(
    name="mail.read",
    tier=Tier.READ,
    params={
        "message_id": "The id in brackets from mail.list",
        "folder": "Mailbox it is in",
        "max_chars": "Truncate the body after this many characters",
    },
)
def mail_read(ctx, message_id: str, folder: str = "INBOX", max_chars: int = 4000) -> str:
    """Read one email in full. Secrets in the body are masked before returning."""
    box = _connect(ctx)
    try:
        status, _ = box.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise ToolError(f"no such mailbox: {folder}")

        status, raw = box.fetch(str(message_id).encode(), "(BODY.PEEK[])")
        if status != "OK" or not raw or not raw[0]:
            raise ToolError(f"no message {message_id} in {folder} (ids change -- run mail.list again)")

        message = email.message_from_bytes(raw[0][1])
        body = _body(message)[:max_chars]
        return ctx.scrub(
            f"From:    {_decode(message.get('From'))}\n"
            f"To:      {_decode(message.get('To'))}\n"
            f"Date:    {_decode(message.get('Date'))}\n"
            f"Subject: {_decode(message.get('Subject'))}\n\n{body}"
        )
    finally:
        _logout(box)


# --------------------------------------------------------------------------
# WRITE
# --------------------------------------------------------------------------

@tool(
    name="mail.draft",
    tier=Tier.WRITE,
    params={"to": "Recipient address", "subject": "Subject line", "body": "Message text"},
    undo="delete it from your Drafts folder",
)
def mail_draft(ctx, to: str, subject: str, body: str) -> str:
    """Save an email to Drafts without sending it. Nobody sees a draft."""
    address, _ = _credentials(ctx)
    if "@" not in to:
        raise ToolError(f"{to!r} does not look like an email address")

    message = _compose(address, to, subject, body)
    box = _connect(ctx)
    try:
        import imaplib as _imaplib
        import time

        status, _ = box.append(
            '"[Gmail]/Drafts"', "\\Draft",
            _imaplib.Time2Internaldate(time.time()),
            message.as_bytes(),
        )
        if status != "OK":
            raise ToolError("Gmail rejected the draft")
    finally:
        _logout(box)

    ctx.log(f"drafted to {to}: {subject[:50]}")
    return f"saved a draft to {to} -- subject {subject!r}. Nothing has been sent."


@tool(
    name="mail.archive",
    tier=Tier.WRITE,
    params={"message_id": "The id from mail.list", "folder": "Mailbox it is in"},
    undo="move it back from All Mail to the Inbox",
)
def mail_archive(ctx, message_id: str, folder: str = "INBOX") -> str:
    """Archive a message: remove it from the Inbox, keep it in All Mail."""
    box = _connect(ctx)
    try:
        status, _ = box.select(f'"{folder}"')
        if status != "OK":
            raise ToolError(f"no such mailbox: {folder}")
        status, _ = box.store(str(message_id).encode(), "+FLAGS", "\\Deleted")
        if status != "OK":
            raise ToolError(f"could not archive {message_id}")
        box.expunge()
    finally:
        _logout(box)

    ctx.log(f"archived {message_id}")
    return f"archived message {message_id} -- it is still in All Mail"


# --------------------------------------------------------------------------
# DANGER
# --------------------------------------------------------------------------

@tool(
    name="mail.send",
    tier=Tier.DANGER,
    params={"to": "Recipient address", "subject": "Subject line", "body": "Message text"},
    undo="nothing. Email cannot be recalled once it has left.",
)
def mail_send(ctx, to: str, subject: str, body: str) -> str:
    """Send an email. ANOTHER PERSON RECEIVES THIS AND IT CANNOT BE UNSENT."""
    address, password = _credentials(ctx)
    if "@" not in to:
        raise ToolError(f"{to!r} does not look like an email address")

    message = _compose(address, to, subject, body)
    try:
        with smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30
        ) as server:
            server.login(address, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise ToolError(f"Gmail refused the login for {address}: {str(exc)[:150]}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise ToolError(f"send failed: {type(exc).__name__}: {str(exc)[:200]}") from exc

    ctx.log(f"SENT to {to}: {subject[:50]}")
    return f"sent to {to} -- subject {subject!r}. This cannot be undone."


def _compose(sender: str, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def _logout(box) -> None:
    try:
        box.logout()
    except Exception:  # noqa: BLE001 -- best effort
        pass
