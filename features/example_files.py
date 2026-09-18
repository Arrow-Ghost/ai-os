"""
REFERENCE FEATURE -- copy this file, do not edit it.

Four tools that show one pattern each:

    files.list   READ    the simplest possible tool
    files.read   READ    handling a path safely
    notes.save   WRITE   a reversible change, with an undo note
    files.trash  DANGER  an action that needs a human to say yes

Delete this file once your team has real features. Nothing depends on it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from servant.sdk import Tier, ToolError, tool


@tool(
    name="files.list",
    tier=Tier.READ,
    params={"path": "Directory to list", "pattern": "Glob pattern, e.g. '*.pdf'"},
)
def files_list(ctx, path: str, pattern: str = "*") -> str:
    """List the files in a directory. Use this before moving or deleting anything."""
    target = ctx.check_path(path)
    if not target.is_dir():
        raise ToolError(f"{target} is not a directory")

    entries = sorted(target.glob(pattern))
    if not entries:
        return f"{target}: nothing matches {pattern!r}"

    lines = [f"{target} ({len(entries)} entries)"]
    for item in entries[:200]:
        size = f"{item.stat().st_size:>9,}" if item.is_file() else "      dir"
        lines.append(f"  {size}  {item.name}")
    if len(entries) > 200:
        lines.append(f"  ... and {len(entries) - 200} more")
    return "\n".join(lines)


@tool(
    name="files.read",
    tier=Tier.READ,
    params={"path": "File to read", "max_chars": "Truncate after this many characters"},
)
def files_read(ctx, path: str, max_chars: int = 4000) -> str:
    """Read a text file. Secrets are masked before the contents are returned."""
    target = ctx.check_path(path)
    if not target.is_file():
        raise ToolError(f"{target} is not a file")

    text = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
    # Always scrub file contents -- this string is about to reach the LLM.
    return ctx.scrub(text)


@tool(
    name="notes.save",
    tier=Tier.WRITE,
    params={"title": "Short name for the note", "body": "What to write"},
    undo="delete the file printed in the result",
)
def notes_save(ctx, title: str, body: str) -> str:
    """Save a note into the agent's own scratch directory. Reversible."""
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    if not safe_title:
        raise ToolError("title must contain at least one letter or number")

    destination = ctx.state_dir("notes") / f"{safe_title}.md"
    destination.write_text(body, encoding="utf-8")

    ctx.memory.remember("notes.last_saved", str(destination))
    ctx.log(f"saved note to {destination}")
    return f"wrote {len(body)} chars to {destination}"


@tool(
    name="files.trash",
    tier=Tier.DANGER,
    params={"path": "File or directory to move to the trash folder"},
    undo="move it back out of .servant/trash/",
)
def files_trash(ctx, path: str) -> str:
    """Move a file to the agent's trash folder. Never deletes anything outright."""
    target = ctx.check_path(path)
    if not target.exists():
        raise ToolError(f"{target} does not exist")

    trash = ctx.state_dir("trash")
    destination = trash / target.name
    counter = 1
    while destination.exists():
        destination = trash / f"{target.stem}.{counter}{target.suffix}"
        counter += 1

    shutil.move(str(target), str(destination))
    ctx.log(f"trashed {target} -> {destination}")
    return f"moved {target} to {destination} (restore with: mv '{destination}' '{target}')"
