# Architecture

## The one-line version

The feature layer is open, the core is closed, and there is exactly one way
for an action to happen.

## Layers

```
  features/            <- your team writes here. Plain functions + @tool.
  ─────────────────────────────────────────────────────────────────────
  servant/sdk.py       <- the only thing features import
  servant/context.py   <- the only capabilities features can reach
  ─────────────────────────────────────────────────────────────────────
  servant/executor.py  <- THE chokepoint. Every action passes through it.
  servant/governance/  <- tiers, approval, redaction, kill switch
  servant/audit.py     <- append-only, hash-chained
  ─────────────────────────────────────────────────────────────────────
  servant/agent.py     <- wiring + the loop
  servant/brain.py     <- Groq, or an offline stub
  servant/memory.py    <- SQLite
  servant/registry.py  <- what the LLM is allowed to see
```

A feature cannot call another feature directly, cannot reach the brain except
through `ctx.think`, and cannot execute a tool except through `ctx.call` —
which re-enters the executor. There is no path around the gate.

## The loop

```
  goal
   │
   ├─> BRAIN        decide(goal, history, tool_schemas) -> ToolCall | Finish
   │
   ├─> EXECUTOR     killswitch -> budget -> lookup -> validate args
   │                -> policy verdict -> approval -> execute
   │
   ├─> AUDIT        gate verdict, then the action and its result
   ├─> MEMORY       episode + outcome
   │
   └─> repeat with the result as the new observation
```

Every arrow above is one function you can read in under a minute. That is the
point: when someone asks "why was the agent allowed to do that?", the answer
is a specific line, not a vibe.

## The four inversions

This agent deliberately does the opposite of the four convergent instrumental
drives, because it runs on a machine you care about.

| Convergent drive | What we built instead | Where |
|---|---|---|
| Self-preservation | **Trivially killable.** Three independent stops, checked before every action. No code resists, delays or routes around a halt. | `governance/killswitch.py` |
| Goal-content integrity | **Trivially correctable.** Goals and limits live in `config/policy.yaml`, which the agent reads and never writes. No mechanism preserves an old goal against your edit. | `config.py` |
| Resource acquisition | **Bounded.** Step, turn and time budgets per run. `max_spend_usd: 0.0`. No self-replication, no reaching to other machines. | `executor.py::Budget` |
| Self-modification | **Static core, extensible edge.** The agent may write NEW TOOLS for itself; it may not touch the executor, the governance layer or the policy. Generated tools are pinned to a tier that always asks. | `validator.py`, `sandbox.py`, `features/skill.py` |

A corrigible agent is not a weaker agent. It is the only kind it is reasonable
to hand real control of a laptop.

## Self-extension

The agent can write its own tools. That is a real capability and it needs to
be described precisely, because it sounds like the thing this project exists
to prevent.

**What it may do:** write a new tool into the feature layer — the same thing a
junior does, automated.

**What it may not do:** modify the executor, the governance layer, the policy
file, the audit log, its own source, or `.git`.

The pipeline:

```
  skill.acquire   research -> draft -> validate -> sandbox -> quarantine
                  (retries, feeding each failure back to the model)
        |
        |   .servant/quarantine is NOT on the feature loader's path.
        |   Nothing here is callable.
        v
  a human reads it   skill.inspect
        |
        v
  skill.install   [DANGER -- asks every time] -> features/
```

Four properties do the work:

1. **Tier floor.** Generated tools are pinned to `DANGER`, so anything the
   agent taught itself asks a human before it runs. The validator rejects any
   generated tool that declares something lower — *tier laundering* is the
   single most important thing it checks. Lowering a tier is a deliberate
   edit a person makes in `config/policy.yaml`, by name.
2. **Quarantine.** Drafts land in `.servant/quarantine`, which the loader does
   not read. Generated code is inert until a human promotes it.
3. **Sandbox.** Drafts execute only in a limited subprocess with a stub
   context — no real memory, no brain, no credentials, CPU/memory/file-size
   capped. A draft is tested from a `.pending` file and only reaches the
   quarantine folder if it passes, with the verdict stamped into its header.
   `skill.install` refuses anything carrying no record of a passing run, so a
   file dropped into quarantine by hand is not installable.
4. **The generated-code gate.** Self-written tools ask before every run even
   under `--full-access`, because otherwise the agent could write a tool and
   run it in the same turn unseen. Governance identifies them from the
   provenance header, not from anything the tool claims about itself. Turning
   this off is one named line in policy: `generated_code_always_asks: false`.

### What the sandbox is not

It is a separate process with `rlimit`s, not a kernel boundary. It does not
block network egress and the code runs as your user. It is a guard against a
*confused* tool, not a malicious one. The real protection is that a human
reads the diff before `skill.install`, and that generated tools always ask.
Doing this properly needs namespaces (bwrap, nsjail) or a container — that is
the production upgrade, and it slots in behind `sandbox.run_tool` without any
feature changing.

Likewise the validator is an AST check, not a proof. It catches the realistic
failure — a model writing something reckless — not a determined adversary.

## Trust boundaries

1. **Machine → LLM.** Everything crossing it passes `Redactor.scrub`. Applied
   in `brain.py` (outbound messages) and `audit.py` (written records).
2. **LLM → machine.** The LLM can only name a registered tool and supply
   arguments. Names are looked up, arguments are validated against the
   declared schema, and the policy verdict happens after both.
3. **Feature → OS.** `ctx.check_path` refuses configured forbidden paths. This
   one is cooperative: a feature that ignores it can still reach the
   filesystem. Reviewing that is what the PR checklist is for.

## What the audit log proves, and what it doesn't

`verify()` detects any record that was edited or deleted from the middle of
the log. It does **not** detect truncation of the tail — chop the last N lines
and the remainder is still a valid chain. Catching that needs an anchor
outside the agent's reach. Say this plainly rather than overselling it.

## Deliberate omissions

Things a production version needs, left out on purpose. Each is a clean
addition to one layer, not a rewrite:

- **LangGraph orchestration** — replaces the loop in `agent.py` with a proper
  state machine, and gives you checkpointing and resumable runs.
- **Semantic memory (ChromaDB)** — sits behind `memory.recall()`. The
  interface already has the right shape.
- **Perception** — screenshots (`mss`), filesystem events (`watchdog`), speech
  (`faster-whisper`). Each becomes a `READ` tool.
- **Scheduling** — `APScheduler`, calling `agent.run()` on a trigger.
- **Sandboxing** — a restricted OS user or container. Today's boundary is
  policy, not the kernel.
- **Desktop GUI automation** — cut on purpose. Wayland blocks synthetic input
  and cross-window capture by design, so the X11 stack (`xdotool`,
  `pyautogui`) does not work on a modern KDE session. The supported route is
  the `xdg-desktop-portal` RemoteDesktop API, which is a project of its own.
  Drive the browser with `browser-use` and the system through code instead;
  neither needs the display server.
