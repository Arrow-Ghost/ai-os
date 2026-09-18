# Writing a feature

Read this once. It is everything you need. You will not have to touch any file
outside `features/`.

---

## 1. The 60-second version

Create `features/weather.py`:

```python
from servant.sdk import tool, Tier, ToolError

@tool(
    name="weather.today",
    tier=Tier.READ,
    params={"city": "City name, e.g. 'Pune'"},
)
def weather_today(ctx, city: str) -> str:
    """Get today's weather for a city. Use when the user asks about weather."""
    ctx.log(f"looking up weather for {city}")
    return f"{city}: 31C, clear"
```

Then:

```bash
python -m servant tools                      # your tool is listed
python -m servant call weather.today city=Pune
```

That is the whole contract. No registration file, no import to add, no core
file to edit. The loader imports everything in `features/` at startup and the
`@tool` decorator does the rest.

---

## 2. The rules the framework enforces

| Rule | Why |
|---|---|
| First argument is always `ctx` | It is your only handle on the framework |
| Every argument needs a type hint | `str`, `int`, `float`, `bool`, `list`, `dict` — the LLM's schema is built from them |
| Arguments must be named (no `*args`/`**kwargs`) | The LLM has to know what to pass |
| Write a docstring, or pass `description=` | This is the LLM's only clue about when to use your tool |
| Pick a `tier` | See below. This decides whether a human is asked |
| Return a string (or anything `str()`-able) | The return value is fed back to the brain |
| Raise `ToolError` on expected failure | The message reaches both the human and the brain |

Break one of these and you get a clear error at startup naming your file. A
broken feature is skipped, not fatal — it never blocks anyone else's work.

---

## 3. Choosing a tier — the one decision that matters

Ask: **if this goes wrong, how bad is it?**

| Tier | Meaning | Examples | What happens |
|---|---|---|---|
| `Tier.READ` | Nothing changes | list a folder, read a file, HTTP GET, query a DB | runs automatically |
| `Tier.WRITE` | Changes something, but you can undo it | write a file, move to trash, create a git branch, save a draft | runs automatically, logged |
| `Tier.DANGER` | Irreversible, costs money, or other people see it | send an email, `git push`, `rm`, install a package, post anything, any payment | **asks the human first** |
| `Tier.FORBIDDEN` | Never runs | placeholder for things policy bans | blocked |

**When unsure, pick the higher one.** Nobody was ever fired for asking
permission. A tool that asks too often is a mild annoyance; a tool that
deletes the wrong folder at 3am is the demo ending.

Three things that are always `DANGER`, no matter how safe they feel:

- anything another person receives (mail, message, post, PR, comment)
- anything that costs money
- anything you cannot undo in under a minute

For `WRITE` and `DANGER`, also pass `undo=` — one line telling the human how
to reverse it. It is printed in the approval prompt, right when they need it.

```python
@tool(
    name="git.push",
    tier=Tier.DANGER,
    params={"branch": "Branch to push"},
    undo="git push --delete origin <branch>",
)
def git_push(ctx, branch: str) -> str:
    """Push a branch to the remote. Other people will see this."""
```

---

## 4. What `ctx` gives you

This is the whole surface. If it is not here, you are not meant to have it —
if you need something more, raise it, do not reach around the framework.

### Talking to the human
```python
ctx.log("moved 12 files")          # prints, and writes to the audit log
ctx.ask("Overwrite the old one?")  # -> bool, blocks until answered
```

### Thinking
```python
answer = ctx.think("Classify this email as urgent/normal: ...")
answer = ctx.think(prompt, smart=True)   # bigger model, slower, better
```
Input is redacted before it leaves the machine. You cannot turn that off.

### Memory (survives across runs)
```python
ctx.memory.remember("inbox.last_seen_id", 4821)
last = ctx.memory.recall("inbox.last_seen_id", default=0)
ctx.memory.forget("inbox.last_seen_id")
```
Namespace your keys with your feature name. Everyone shares one store.

### Calling another tool
```python
result = ctx.call("files.list", path="/tmp")
if result.ok:
    print(result.output)
```
Still goes through governance — a `DANGER` tool called from your tool still
asks the human. Compose features this way instead of importing each other.

### Files and paths
```python
path = ctx.check_path(user_supplied_path)   # ALWAYS do this first
folder = ctx.state_dir("myfeature")         # private scratch dir, auto-created
```
`ctx.check_path` refuses anything under `~/.ssh`, `~/.aws`, `.env` and friends,
and returns a resolved `Path`. **Call it on every path argument.** It is the
one guard the framework cannot apply for you, because only your tool knows
which argument is a path.

### Secrets
```python
token = ctx.secret("GITHUB_TOKEN")   # reads .env / environment
if not token:
    raise ToolError("GITHUB_TOKEN is not set -- add it to .env")
```
Never hardcode a key. Never put one in `config/policy.yaml`.

### Scrubbing
```python
return ctx.scrub(file_contents)   # mask secrets before returning to the LLM
```
Do this on anything you read from disk or the network.

---

## 5. Testing your feature

```bash
python -m servant tools -v                    # is it registered, right schema?
python -m servant call myfeature.thing arg=x  # run it for real, through governance
python -m servant audit -n 10                 # what did it actually do?
```

And a unit test in `tests/` — copy the shape from `tests/test_executor.py`:

```python
from servant.contracts import Status

def test_my_tool(agent):
    import features.myfeature  # noqa: F401  (registers the tool)
    result = agent.call_tool("myfeature.thing", {"arg": "x"})
    assert result.status is Status.OK
```

The `agent` fixture is offline, uses a temp directory, and denies anything
needing a human — so `DANGER` tools are safe to test.

---

## 6. Before you open a PR

- [ ] Tier is right, and is the higher one if you hesitated
- [ ] `undo=` set for every `WRITE` and `DANGER` tool
- [ ] `ctx.check_path()` on every path argument
- [ ] Docstring explains **when** to use it, not just what it does
- [ ] Every param has a description in `params={}`
- [ ] `ToolError` for expected failures, with a message that says what to fix
- [ ] Tool name is `area.verb` and unique
- [ ] Tested against throwaway data — a scratch folder, a test label, a junk repo
- [ ] One test in `tests/`

---

## 7. Common mistakes

**Doing the dangerous thing inside a READ tool.** The tier describes what the
function *does*, not what it is called. A tool named `files.check` that also
deletes is a `DANGER` tool with a misleading name. Split it in two.

**Swallowing errors and returning `"ok"`.** The brain believes you. It will
build its next three steps on a lie. Raise `ToolError` instead.

**Returning 40,000 characters.** It all goes into the next LLM call. Summarise,
truncate, or return a path to the full output.

**Asking the human inside the function instead of using a tier.** If the whole
action needs approval, that is `Tier.DANGER`. Use `ctx.ask()` only for a
genuine mid-action branch ("three files match, use the newest?").

**Importing another feature module.** Use `ctx.call("other.tool", ...)` so the
call is governed and logged like everything else.

**Writing to `config/policy.yaml` from a tool.** Don't. The owner's policy is
not the agent's to edit — that is the property that makes this whole thing
safe to run.
