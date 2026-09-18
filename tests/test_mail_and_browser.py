"""Mail and browser features. Fully offline -- no logins, no network, no browser."""

import pytest

from servant.contracts import Status, Tier


# -- tiers ----------------------------------------------------------------

@pytest.mark.parametrize("module,tool_name,expected", [
    ("mail", "mail.list", Tier.READ),
    ("mail", "mail.read", Tier.READ),
    ("mail", "mail.draft", Tier.WRITE),
    ("mail", "mail.archive", Tier.WRITE),
    ("mail", "mail.send", Tier.DANGER),
    ("browser", "browser.task", Tier.DANGER),
])
def test_tiers(feature, module, tool_name, expected):
    """Sending mail and driving a browser reach other people. Both must ask."""
    from servant.registry import REGISTRY

    feature(module)
    assert REGISTRY.get(tool_name).tier is expected


# -- credential handling --------------------------------------------------

def test_missing_address_is_explained(agent, feature, monkeypatch):
    feature("mail")
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd" * 4)

    result = agent.call_tool("mail.list", {})
    assert result.status is Status.ERROR
    assert "GMAIL_ADDRESS" in result.error


def test_account_password_is_rejected(agent, feature, monkeypatch):
    """An app password is exactly 16 chars; anything else is the wrong secret."""
    feature("mail")
    monkeypatch.setenv("GMAIL_ADDRESS", "a@b.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "my-real-account-password")

    result = agent.call_tool("mail.list", {})
    assert result.status is Status.ERROR
    assert "16 characters" in result.error


def test_spaces_in_app_password_are_tolerated(feature, monkeypatch):
    """Google displays them in four groups; people paste them that way."""
    mod = feature("mail")
    monkeypatch.setenv("GMAIL_ADDRESS", "a@b.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")

    class Ctx:
        def secret(self, name):
            import os
            return os.environ.get(name)

    address, password = mod._credentials(Ctx())
    assert password == "abcdefghijklmnop"


def test_bad_recipient_rejected_before_connecting(yes_agent, feature, monkeypatch):
    """Validation must happen before any network call or login attempt."""
    feature("mail")
    monkeypatch.setenv("GMAIL_ADDRESS", "a@b.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd" * 4)

    result = yes_agent.call_tool(
        "mail.send", {"to": "not-an-address", "subject": "x", "body": "y"}
    )
    assert result.status is Status.ERROR
    assert "email address" in result.error


def test_app_password_is_redacted(config):
    """It must never reach the model or the audit log."""
    from servant.governance import Redactor

    redactor = Redactor.from_config(config)
    scrubbed = redactor.scrub("my gmail app password is fbzr ywmh wyke jybf ok")
    assert "fbzr ywmh wyke jybf" not in scrubbed


# -- body parsing ---------------------------------------------------------

def test_plain_text_body_preferred_over_html(feature):
    import email

    mod = feature("mail")
    raw = (
        "From: a@b.com\nSubject: Test\nMIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="B"\n\n'
        "--B\nContent-Type: text/plain\n\nplain version\n"
        "--B\nContent-Type: text/html\n\n<p>html version</p>\n--B--\n"
    )
    assert "plain version" in mod._body(email.message_from_string(raw))


def test_html_only_body_is_stripped_of_tags(feature):
    import email

    mod = feature("mail")
    raw = (
        "From: a@b.com\nSubject: Test\nMIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="B"\n\n'
        "--B\nContent-Type: text/html\n\n<p>hello <b>there</b></p>\n--B--\n"
    )
    body = mod._body(email.message_from_string(raw))
    assert "hello" in body and "<p>" not in body


def test_encoded_subject_is_decoded(feature):
    mod = feature("mail")
    assert mod._decode("=?utf-8?q?Caf=C3=A9_meeting?=") == "Café meeting"


def test_malformed_header_does_not_raise(feature):
    mod = feature("mail")
    assert mod._decode("=?broken?!!?=") is not None


# -- browser --------------------------------------------------------------

def test_browser_rejects_empty_task(yes_agent, feature):
    feature("browser")
    result = yes_agent.call_tool("browser.task", {"task": "   "})
    assert result.status is Status.ERROR


def test_browser_rejects_absurd_step_count(yes_agent, feature):
    feature("browser")
    result = yes_agent.call_tool("browser.task", {"task": "do a thing", "max_steps": 500})
    assert result.status is Status.ERROR
    assert "max_steps" in result.error


def test_browser_secrets_must_name_an_env_var(yes_agent, feature, monkeypatch):
    feature("browser")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    result = yes_agent.call_tool(
        "browser.task", {"task": "log in", "secrets_env": "password=NOT_SET_ANYWHERE"}
    )
    assert result.status is Status.ERROR
    assert "NOT_SET_ANYWHERE" in result.error
