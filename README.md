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
cp .env.example .env         # add your GROQ_API_KEY
# then in config/policy.yaml:  brain.provider: groq
python -m servant run "list the python files in servant/ and save a summary note"
```

Until you do that, `brain.provider: offline` is the default and needs no API
key — so your team can build and test features on day one without one.

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

Working: registry, feature autoloading, permission tiers, human approval,
redaction, hash-chained audit log, kill switch, budgets, SQLite memory, the
agent loop, Groq and offline brains, CLI, 34 tests.

Not built yet, on purpose: perception, scheduling, browser control, semantic
memory, sandboxing. See the deliberate-omissions section of
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — each one is an addition to a
single layer, not a rewrite.
