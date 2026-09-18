"""
notes.* -- the agent's own scratchpad, kept under .servant/features/notes.
"""

from __future__ import annotations

from servant.sdk import Tier, ToolError, tool


@tool(
    name="notes.save",
    tier=Tier.WRITE,
    params={"title": "Short name for the note", "body": "What to write"},
    undo="files.trash the file printed in the result",
)
def notes_save(ctx, title: str, body: str) -> str:
    """Save a note into the agent's own scratch directory. Use it to keep a summary or result."""
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    if not safe_title:
        raise ToolError("title must contain at least one letter or number")

    destination = ctx.state_dir("notes") / f"{safe_title}.md"
    if destination.exists():
        raise ToolError(f"a note called {safe_title!r} already exists -- pick another title")
    destination.write_text(body, encoding="utf-8")

    ctx.memory.remember("notes.last_saved", str(destination))
    ctx.log(f"saved note to {destination}")
    return f"wrote {len(body)} chars to {destination}"
