"""
Browser control, via browser-use driving real Chrome over CDP.

Why this and not GUI automation: browser-use reads the page's DOM and decides
what to click, so it needs no synthetic input and no screen capture. That is
what makes it work on Wayland, where injecting clicks into another window is
blocked by design.

TIER: DANGER, and it stays there. A browser task can log in, submit forms,
send messages, buy things and accept terms on your behalf. It is the single
most consequential tool in this repo, and unlike every other tool here it
decides its own sequence of actions once it starts.

SCOPE IT. `allowed_domains` is the seatbelt -- browser-use refuses to
navigate anywhere else. Always pass it for anything touching an account:

    browser.task task="check the build status" allowed_domains="github.com"

CREDENTIALS. Never put a password in `task`. Use `secrets_env`, which passes
values through browser-use's sensitive_data mechanism: the model sees a
placeholder like <secret>gh_pw</secret>, never the value, and the value is
masked in its logs.

TWO TOOLS, PICK BY TOKEN BUDGET.
  browser.browse  a small loop written here: a compact page summary (~1k
                  tokens a step) and five actions. Fits inside a free tier,
                  runs a step in about a second, and goes through ctx.think
                  so it inherits key rotation and redaction. Start here.
  browser.task    full browser-use. Far more capable on complex sites, and
                  far more expensive -- see below.

TOKEN BUDGET -- READ THIS BEFORE DEMOING browser.task.
browser-use sends a large system prompt plus the page's clickable-element tree
on EVERY step. Measured against Groq's free tier, which allows 8,000 tokens
per minute, a single step consumes roughly the whole minute's allowance, even
on a page as small as example.com. The caps set below (history, element-tree
length, attribute list) help but do not close the gap.

So on a free Groq key this tool works, slowly and unreliably: about one step
per minute, with 413s in between. That is a tier limit and NOT something a
second API key fixes -- the request is too large, not too frequent. For a live
demo either raise the Groq tier, point this at another provider, or use
web.fetch / web.search, which cost a fraction of the tokens.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

from servant.sdk import Tier, ToolError, tool

MAX_ALLOWED_STEPS = 40


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@tool(
    name="browser.task",
    tier=Tier.DANGER,
    params={
        "task": "What to do, in plain English. Be specific about when to stop.",
        "allowed_domains": "Comma-separated domains it may visit, e.g. 'github.com,docs.python.org'. Empty means anywhere.",
        "max_steps": "Most actions to take before giving up (1-40)",
        "headless": "True to hide the browser. False lets you watch and intervene.",
        "secrets_env": "Comma-separated NAME=ENV_VAR pairs. The model sees a placeholder, never the value.",
        "vision": "True to let it look at screenshots. Slower and pricier; needed for canvas-heavy sites.",
    },
    undo="depends entirely on what it did -- check the browser and the sites it visited",
    untrusted=True,
)
def browser_task(
    ctx,
    task: str,
    allowed_domains: str = "",
    max_steps: int = 15,
    headless: bool = False,
    secrets_env: str = "",
    vision: bool = False,
) -> str:
    """Drive a real web browser to complete a task. It decides its own clicks.

    Use when no API exists for what is needed. Prefer a real API when there is
    one -- it is faster, cheaper and far more predictable than a browser.
    """
    if not task.strip():
        raise ToolError("task is empty -- say what the browser should do")
    if not 1 <= max_steps <= MAX_ALLOWED_STEPS:
        raise ToolError(f"max_steps must be between 1 and {MAX_ALLOWED_STEPS}")

    api_key = ctx.secret("GROQ_API_KEY")
    if not api_key:
        raise ToolError("GROQ_API_KEY is not set -- browser.task needs a model to plan with")

    try:
        from browser_use import Agent, BrowserProfile, ChatGroq
    except ImportError as exc:
        raise ToolError(
            "browser-use is not installed. pip install browser-use, and make sure "
            "Chrome or Chromium is on the system."
        ) from exc

    domains = _split(allowed_domains)
    if not domains:
        ctx.log("WARNING: no allowed_domains -- this browser can navigate anywhere")

    # Resolve secrets from the environment here, so the values never appear in
    # the task string, the audit log or the approval prompt.
    sensitive: dict[str, str] = {}
    for pair in _split(secrets_env):
        if "=" not in pair:
            raise ToolError(f"secrets_env entries must be NAME=ENV_VAR, got {pair!r}")
        name, _, env_var = pair.partition("=")
        value = ctx.secret(env_var.strip())
        if not value:
            raise ToolError(f"{env_var.strip()} is not set in .env")
        sensitive[name.strip()] = value

    profile = BrowserProfile(
        headless=bool(headless),
        allowed_domains=domains or None,
        executable_path=_chrome_path(),
    )
    # Two constraints pick the model here.
    #
    # 1. Text-only models reject the multimodal message browser-use builds when
    #    vision is on ("content must be a string"), so vision has to pair with
    #    the vision model.
    # 2. browser-use ships the whole DOM on every step, which is enormous. On
    #    Groq's free tier that blows the tokens-per-minute ceiling of the big
    #    model within a couple of steps and the run limps along on retries.
    #    The small model has far more TPM headroom and is perfectly capable of
    #    "click the third link", which is what this job actually is.
    model = (
        ctx.config.get("brain.model_vision", "qwen/qwen3.8-27b") if vision
        else ctx.config.get("brain.model_fast", "openai/gpt-oss-20b")
    )
    llm = ChatGroq(model=model, api_key=api_key, temperature=0.0)

    scope = ", ".join(domains) if domains else "ANY SITE"
    ctx.log(f"browser task ({scope}, {model}, max {max_steps} steps): {task[:90]}")

    try:
        history = asyncio.run(_run(task, llm, profile, sensitive, max_steps, vision))
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 -- browser-use raises a wide range
        raise ToolError(f"browser task failed: {type(exc).__name__}: {exc}"[:800]) from exc

    summary = _summarise(history, max_steps)

    hit_ceiling = "TPM" in summary or "too large" in summary.lower()
    completed = summary.startswith("completed")

    # If it finished anyway, hand back the answer and just flag the cost --
    # raising on a task that succeeded would be plainly wrong.
    if completed:
        note = (
            "\n\nNOTE: this run hit Groq's 8,000 tokens-per-minute ceiling and only "
            "got through on retries. It will be slow and flaky until the tier is "
            "raised. See the TOKEN BUDGET note in features/browser.py."
        ) if hit_ceiling else ""
        return ctx.scrub(summary + note)

    # Turn the characteristic free-tier failure into a straight answer rather
    # than a quiet "did not complete", which reads like the task was merely
    # hard. See the TOKEN BUDGET note in the module docstring.
    if hit_ceiling:
        raise ToolError(
            "browser.task hit Groq's per-minute token ceiling (8,000 TPM on the free "
            "tier). browser-use sends a large system prompt plus the page's element "
            "tree on every step, so a single step can consume the whole minute's "
            "budget -- the run then crawls at roughly one step per minute.\n\n"
            "This is a tier limit, not a bug, and another API key will not fix it "
            "(the request is too large, not too frequent). Options: raise the Groq "
            "tier, point browser.task at a different provider, or use web.fetch and "
            "web.search, which cost a fraction of the tokens.\n\n"
            f"What it managed before stopping:\n{summary}"
        )
    return ctx.scrub(summary)


async def _run(task, llm, profile, sensitive, max_steps, vision):
    # Imported here as well as in the caller: this runs in module scope, where
    # the caller's local import is not visible.
    from browser_use import Agent

    agent = Agent(
        task=task,
        llm=llm,
        browser_profile=profile,
        sensitive_data=sensitive or None,
        use_vision=vision,
        # Groq's free tier allows 8,000 tokens PER MINUTE, and browser-use
        # ships the page's whole clickable-element tree on every step. Left
        # alone, one step exceeds the entire minute's budget and the run dies
        # on 413s. These three caps are what keep a step inside it.
        max_history_items=6,   # browser-use requires > 5
        max_clickable_elements_length=6000,
        include_attributes=["title", "type", "name", "aria-label", "placeholder"],
        # Do not let browser-use install its own SIGINT handler: Ctrl-C belongs
        # to the kill switch, and a library quietly taking it would make the
        # agent harder to stop.
        enable_signal_handler=False,
    )
    try:
        return await agent.run(max_steps=max_steps)
    finally:
        # Always close the browser, even on failure, or Chrome is left running.
        for closer in ("close", "kill", "stop"):
            method = getattr(getattr(agent, "browser_session", None), closer, None)
            if callable(method):
                try:
                    result = method()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001 -- best effort cleanup
                    pass
                break


def _summarise(history, max_steps: int) -> str:
    """browser-use returns a rich history object; pull out what a human needs."""
    def attempt(name, default=None):
        method = getattr(history, name, None)
        try:
            return method() if callable(method) else default
        except Exception:  # noqa: BLE001
            return default

    result = attempt("final_result")
    done = attempt("is_done", False)
    errors = [e for e in (attempt("errors", []) or []) if e]
    urls = attempt("urls", []) or []
    steps = len(attempt("model_actions", []) or [])

    lines = [f"{'completed' if done else 'did not complete'} in {steps}/{max_steps} step(s)"]
    if result:
        lines.append(f"\nresult:\n{str(result)[:2000]}")
    if urls:
        unique = list(dict.fromkeys(str(u) for u in urls if u))[-5:]
        lines.append("\nvisited: " + ", ".join(unique))
    if errors:
        lines.append("\nerrors: " + "; ".join(str(e)[:200] for e in errors[:3]))
    return "\n".join(lines)


def _chrome_path() -> str | None:
    import shutil

    for name in ("google-chrome", "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --------------------------------------------------------------------------
# The lightweight loop
# --------------------------------------------------------------------------

EXTRACT_JS = """() => {
  const out = [];
  const nodes = document.querySelectorAll('a,button,input,textarea,select,[role=button]');
  for (const n of nodes) {
    if (out.length >= 40) break;
    const r = n.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;          // skip hidden
    const label = (n.innerText || n.value || n.placeholder ||
                   n.getAttribute('aria-label') || n.name || '').trim().slice(0, 60);
    if (!label && n.tagName !== 'INPUT') continue;
    out.push({
      tag: n.tagName.toLowerCase(),
      type: n.type || '',
      label: label,
      href: (n.getAttribute('href') || '').slice(0, 80),
    });
  }
  return {
    elements: out,
    text: (document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 700),
  };
}"""

ACTION_RULES = """Reply with ONE JSON object and nothing else:
{"action":"click","index":N}
{"action":"type","index":N,"text":"..."}
{"action":"goto","url":"https://..."}
{"action":"scroll"}
{"action":"done","answer":"what you found, or why you stopped"}
Pick "done" as soon as the task is answered. Do not explain."""


def _describe(page) -> tuple[str, list]:
    """Compact page state. Every token here is paid for on every single step."""
    data = page.evaluate(EXTRACT_JS)
    elements = data.get("elements", [])
    lines = [f"URL: {page.url}", f"TITLE: {page.title()[:80]}", "ELEMENTS:"]
    for index, element in enumerate(elements):
        bits = f"[{index}] {element['tag']}"
        if element.get("type"):
            bits += f":{element['type']}"
        if element.get("label"):
            bits += f' "{element["label"]}"'
        if element.get("href"):
            bits += f" -> {element['href']}"
        lines.append(bits)
    lines.append(f"TEXT: {data.get('text', '')}")
    return "\n".join(lines), elements


def _decide(ctx, task: str, state: str, history: list[str]) -> dict:
    recent = "\n".join(history[-3:])
    prompt = (
        f"You are operating a web browser to finish this task.\nTASK: {task}\n\n"
        + (f"ALREADY DONE:\n{recent}\n\n" if recent else "")
        + f"CURRENT PAGE:\n{state}\n\n{ACTION_RULES}"
    )
    raw = ctx.think(prompt)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ToolError(f"the model did not return an action: {raw[:200]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ToolError(f"unparseable action {match.group(0)[:200]}: {exc}") from exc


def _host_allowed(url: str, domains: list[str]) -> bool:
    if not domains:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


@tool(
    name="browser.browse",
    tier=Tier.DANGER,
    params={
        "task": "What to find or do, in plain English",
        "start_url": "The page to open first",
        "allowed_domains": "Comma-separated domains it may visit. Empty means anywhere.",
        "max_steps": "Most actions before giving up (1-20)",
        "headless": "True to hide the browser, False to watch it work",
    },
    undo="depends what it did -- check the sites it visited",
    untrusted=True,
)
def browser_browse(
    ctx,
    task: str,
    start_url: str,
    allowed_domains: str = "",
    max_steps: int = 8,
    headless: bool = True,
) -> str:
    """Browse the web to answer a question. Cheap enough for a small token budget.

    Use this instead of browser.task when the token ceiling matters. It sends a
    compact page summary -- roughly 1k tokens a step against browser.task's 8k+
    -- so it fits inside a free-tier per-minute allowance.
    """
    if not task.strip():
        raise ToolError("task is empty")
    if not start_url.lower().startswith(("http://", "https://")):
        raise ToolError(f"start_url must be http(s), got {start_url!r}")
    if not 1 <= max_steps <= 20:
        raise ToolError("max_steps must be between 1 and 20")

    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
    except ImportError as exc:
        raise ToolError("playwright is not installed. pip install playwright") from exc

    domains = _split(allowed_domains)
    if not domains:
        ctx.log("WARNING: no allowed_domains -- this browser can navigate anywhere")
    if not _host_allowed(start_url, domains):
        raise ToolError(f"{start_url} is outside allowed_domains ({', '.join(domains)})")

    history: list[str] = []
    answer = ""

    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=bool(headless))
        page = browser.new_page()
        try:
            page.goto(start_url, timeout=25000, wait_until="domcontentloaded")

            for step in range(1, max_steps + 1):
                # The kill switch is checked between every step, so a long
                # browse stops the same way everything else does.
                ctx.killswitch_check()

                state, elements = _describe(page)
                action = _decide(ctx, task, state, history)
                kind = str(action.get("action", "")).lower()

                if kind == "done":
                    answer = str(action.get("answer", "")).strip()
                    history.append(f"step {step}: done")
                    break

                if kind == "goto":
                    url = str(action.get("url", ""))
                    if not _host_allowed(url, domains):
                        history.append(f"step {step}: REFUSED {url} (outside allowed_domains)")
                        continue
                    page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    history.append(f"step {step}: went to {url}")

                elif kind in {"click", "type"}:
                    index = int(action.get("index", -1))
                    if not 0 <= index < len(elements):
                        history.append(f"step {step}: no element [{index}]")
                        continue
                    handle = page.query_selector_all(
                        "a,button,input,textarea,select,[role=button]"
                    )[index]
                    if kind == "click":
                        handle.click(timeout=10000)
                        history.append(f"step {step}: clicked [{index}]")
                    else:
                        handle.fill(str(action.get("text", "")), timeout=10000)
                        history.append(f"step {step}: typed into [{index}]")
                    page.wait_for_timeout(1200)

                elif kind == "scroll":
                    page.mouse.wheel(0, 900)
                    history.append(f"step {step}: scrolled")

                else:
                    history.append(f"step {step}: unknown action {kind!r}")

                if not _host_allowed(page.url, domains):
                    history.append(f"step {step}: left the allowed domains -- going back")
                    page.go_back(timeout=15000)

        except PWTimeout as exc:
            raise ToolError(f"the page timed out: {str(exc)[:200]}") from exc
        finally:
            browser.close()

    trail = "\n".join(f"  {line}" for line in history)
    if answer:
        return ctx.scrub(f"{answer}\n\nsteps taken:\n{trail}")
    return ctx.scrub(
        f"stopped after {len(history)} step(s) without a final answer.\n\nsteps taken:\n{trail}"
    )
