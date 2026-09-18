# servant

A laptop agent with a governance layer you can point at.

It sees, thinks on an LLM, and acts through code — but it asks before doing
anything irreversible, redacts secrets before they leave the machine, logs
every action to an append-only file, and stops the instant you tell it to.

**This repo is the skeleton.** The framework is done; the features are not.
The whole design is that adding a capability means writing one plain function
in `features/` and nothing else.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m servant doctor     # is this machine set up?
python -m servant tools      # what can it do?
python -m servant call files.list path=.
```

The framework runs on the standard library alone, so the first two commands
work before you install anything. You only need `openai` when you switch the
brain on, and `PyYAML` to edit `config/policy.yaml`.

### Turning the brain on

```bash
cp .env.example .env         # add GROQ_API_KEY, or GROQ_API_KEYS for several
# then in config/policy.yaml:  brain.provider: groq
python -m servant run "read servant/registry.py and save a summary note"
```

Set `GROQ_API_KEYS` to a comma-separated list and the brain rotates to the
next key on a rate limit — Groq's free tier is low enough to run out
mid-demo.

`brain.provider: offline` needs no API key at all, so your team can build and
test features without one.

### Models

Verified live on Groq (their model list changes — re-check before a demo):

| Role | Model |
|---|---|
| routing, short steps | `openai/gpt-oss-20b` |
| planning | `openai/gpt-oss-120b` |
| vision (`screen.*`) | `qwen/qwen3.8-27b` |
| speech (`speech.*`) | `whisper-large-v3-turbo` |

`groq/compound*` (the models with built-in web search) return **413** on this
account's tier, so `web.search` needs a separate provider key.

---

## Commands

| Command | What it does |
|---|---|
| `python -m servant tools [-v]` | list every capability, grouped by tier |
| `python -m servant call <tool> k=v` | run one tool directly, through governance |
| `python -m servant run "<goal>"` | give the agent a goal and let it loop |
| `python -m servant stop` / `go` | engage / release the kill switch |
| `python -m servant audit -n 20` | tail the audit log |
| `python -m servant audit --verify` | check the log's hash chain |
| `python -m servant watch` | run registered monitors in the background |
| `python -m servant watch --once` | one pass over every monitor (for cron) |
| `python -m servant memory` | what the agent remembers |
| `python -m servant doctor` | environment check |

Useful during a demo: `tail -f .servant/audit.jsonl` in a second terminal.

---

## How it stops

Three independent ways, any one of which is enough:

```bash
touch .servant/STOP        # or: python -m servant stop
Ctrl-C                     # same flag, set in-process
pkill -f servant           # the OS always wins
```

The flag is checked before every single tool call. Nothing in the agent
resists, delays, or routes around a stop — that is the property that makes it
reasonable to give it real access.

---

## The permission model

Every tool declares how dangerous it is. The policy maps that to behaviour,
and you own the policy.

| Tier | Meaning | Default |
|---|---|---|
| `READ` | nothing changes | runs automatically |
| `WRITE` | reversible change | runs automatically, logged |
| `DANGER` | irreversible, costs money, or other people see it | **asks you first** |
| `FORBIDDEN` | banned outright | blocked |

Edit `config/policy.yaml` to tighten or loosen it. Your `tier_overrides` beat
whatever a feature author declared, and `denylist` beats everything.

The one-sentence pitch: **it does the reading, running, drafting and watching;
you keep the sending, spending and deleting.**

---

## What it can do today

| Area | Tools | Tier |
|---|---|---|
| Notifications | `notify.send` | WRITE |
| Clipboard | `clipboard.read` `clipboard.write` | READ / WRITE |
| Web | `web.fetch` | READ |
| Monitors | `watch.check_file` `watch.check_log` `watch.check_endpoint` `watch.add` `watch.list` `watch.remove` | READ / WRITE |
| Screen | `screen.capture` | READ |
| Screen → LLM | `screen.describe` `screen.read_text` | **DANGER** |
| Speech | `speech.record` `speech.transcribe` | **DANGER** |
| Files (example) | `files.list` `files.read` `notes.save` `files.trash` | READ / WRITE / DANGER |

Anything that opens a sensor, or sends something off the machine that
**cannot be redacted** (an image, an audio file), is tiered DANGER and asks
every time. Lower them deliberately in `config/policy.yaml` if a prompt is in
the way of your demo.

### Monitoring, cheaply

```bash
python -m servant call watch.add kind=log target=/var/log/build.log interval=30 pattern=ERROR
python -m servant watch
```

Polling is pure Python — `stat`, a file read, an HTTP GET — so a watcher left
running costs nothing. The brain is only involved when a monitor fires *and*
that monitor was given a `goal`. Every poll still goes through the executor,
so background work is gated and audited exactly like interactive work.

### Wayland

This is built for a Wayland session (KDE). Wayland deliberately stops a client
reading another window's pixels or injecting input, so `mss`, `pyautogui` and
`xdotool` do not work — `screen.*` shells out to `spectacle`/`grim` instead.
Desktop GUI automation is out of scope by design; drive the browser and the
system through code instead.

## Adding a feature

```python
# features/weather.py
from servant.sdk import tool, Tier

@tool(name="weather.today", tier=Tier.READ, params={"city": "City name"})
def weather_today(ctx, city: str) -> str:
    """Get today's weather for a city. Use when the user asks about weather."""
    return f"{city}: 31C, clear"
```

Drop the file in `features/`, run `python -m servant tools`, and it is live.
No registration, no imports to update, no core file to touch.

→ **[docs/FEATURE_GUIDE.md](docs/FEATURE_GUIDE.md)** is the full contract.
It is short. Read it before your first feature.

---

## Layout

```
servant/            the framework. Closed — changes here need review.
  sdk.py            what features import
  context.py        what features can reach
  executor.py       the one chokepoint every action passes through
  governance/       tiers, approval, redaction, kill switch
  agent.py          wiring + the loop
features/           what your team writes. Open.
config/policy.yaml  your rules. The agent reads this and never writes it.
docs/               FEATURE_GUIDE.md (juniors) · ARCHITECTURE.md (the why)
tests/              run with: python -m pytest tests -q
.servant/           audit log, memory db, trash. Gitignored.
```

---

## Status

**Framework**: registry, feature autoloading, permission tiers, human
approval, redaction, hash-chained audit log, kill switch, budgets, SQLite
memory, the agent loop, Groq (vision + speech + key rotation) and offline
brains, CLI, background watcher. 77 tests.

**Track B features** (perception and the outside world): notifications,
clipboard, web fetch, monitors, screen capture + vision, speech. Done.

**Track A features** (local hands): `files.*` beyond the example, `git.*`,
`organize.*`, `shell.*`, `code.*`, `sys.*`. Not started — see
[features/README.md](features/README.md) for the prefix table.

**Still open in Track B**: `web.search` (needs a provider key), `browser.*`
(browser-use), `mail.*` (needs Gmail OAuth).

Not built, on purpose: semantic memory, sandboxing, LangGraph orchestration,
desktop GUI automation. See the deliberate-omissions section of
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — each is an addition to a single
layer, not a rewrite.
