"""
Durable memory: facts the agent should not have to re-ask for.

NOT semantic search. There is no embedding model or vector store here --
that was scoped out deliberately (see docs/ARCHITECTURE.md). memory.recall is
keyword/substring search over what memory.remember has stored, backed by the
same SQLite file every other feature already uses. Honest about what it is:
exact and substring recall of facts you told it to keep, not fuzzy-meaning
search over everything it has ever seen.

Facts live under the "fact." prefix, kept separate from the internal
namespaced state other features use (watch.monitors, screen.last_capture,
etc.) so memory.recall does not surface plumbing you never asked it to keep.
"""

from __future__ import annotations

from servant.sdk import Tier, ToolError, tool

PREFIX = "fact."


@tool(
    name="memory.remember",
    tier=Tier.WRITE,
    params={
        "key": "Short name for this fact, e.g. 'project.deadline' or 'preference.editor'",
        "value": "What to remember",
    },
    undo="memory.forget with the same key",
)
def memory_remember(ctx, key: str, value: str) -> str:
    """Store a durable fact -- project context, a preference, a decision. Recall it later with memory.recall."""
    key = key.strip()
    if not key:
        raise ToolError("key is empty")
    ctx.memory.remember(PREFIX + key, value)
    ctx.log(f"remembered {key!r}")
    return f"remembered {key!r}"


@tool(
    name="memory.recall",
    tier=Tier.READ,
    params={
        "query": "What to look for. Leave empty to list everything remembered.",
        "limit": "Most results to return",
    },
)
def memory_recall(ctx, query: str = "", limit: int = 20) -> str:
    """Look up facts previously stored with memory.remember. Keyword match, not semantic search."""
    limit = max(1, min(int(limit), 100))
    if not query.strip():
        rows = ctx.memory.search_facts("", prefix=PREFIX, limit=limit)
    else:
        rows = ctx.memory.search_facts(query, prefix=PREFIX, limit=limit)

    if not rows:
        return f"nothing remembered matching {query!r}" if query else "nothing remembered yet"

    lines = [f"{len(rows)} fact(s):"]
    for row in rows:
        key = row["key"][len(PREFIX):]
        lines.append(f"  {key}: {row['value']}")
    return ctx.scrub("\n".join(lines))


@tool(
    name="memory.forget",
    tier=Tier.WRITE,
    params={"key": "The fact to delete, as given to memory.remember"},
    undo="memory.remember it again -- once gone, the agent cannot recover a forgotten fact",
)
def memory_forget(ctx, key: str) -> str:
    """Delete a remembered fact. You can delete anything, any time -- the agent cannot resist or restore it."""
    key = key.strip()
    full_key = PREFIX + key
    if ctx.memory.recall(full_key) is None:
        raise ToolError(f"no fact named {key!r} (see memory.recall)")
    ctx.memory.forget(full_key)
    ctx.log(f"forgot {key!r}")
    return f"forgot {key!r}"
