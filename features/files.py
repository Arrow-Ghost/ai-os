"""
files.* -- look at, move, copy and trash files. Nothing here deletes.

    files.list     READ    what is in a folder
    files.read     READ    the text of one file, secrets masked
    files.move     WRITE   move or rename; never overwrites
    files.copy     WRITE   copy; never overwrites
    files.mkdir    WRITE   make a folder
    files.trash    DANGER  move into .servant/trash, with a record of where it came from
    files.restore  WRITE   put a trashed item back (or list the trash)

The rule for this module: every change can be walked back with another call
to this module. That is why there is no files.delete and no overwrite flag.
"""

from __future__ import annotations

import itertools
import json
import shutil
import time
import uuid
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

LIST_LIMIT = 200
SCAN_LIMIT = 5000
READ_LIMIT = 20_000
SNIFF_BYTES = 8192


# -- helpers ---------------------------------------------------------------

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _is_forbidden(ctx, path: Path) -> bool:
    try:
        ctx.check_path(path)
    except ToolError:
        return True
    return False


def _refuse_precious(ctx, target: Path, verb: str) -> None:
    """Some folders are never the thing you meant to move."""
    precious = {
        Path(target.anchor),
        Path.home().resolve(),
        ctx.root.resolve(),
        ctx.config.path("state_dir").resolve(),
    }
    if target in precious:
        raise ToolError(f"refusing to {verb} {target} -- that is a drive, home, or the agent itself")


def _trash_dir(ctx) -> Path:
    d = ctx.config.path("state_dir") / "trash"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trash_entries(ctx) -> list[dict]:
    entries = []
    for meta in sorted(_trash_dir(ctx).glob("*.json"), reverse=True):
        try:
            entries.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return entries


def _resolve_destination(src: Path, dst: Path) -> Path:
    """`mv a dir/` means `dir/a`. Refuse anything that would overwrite."""
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"{dst} already exists -- pick another name or trash it first")
    if src.is_dir() and (dst == src or src in dst.parents):
        raise ToolError(f"cannot put {src} inside itself")
    if not dst.parent.is_dir():
        raise ToolError(f"{dst.parent} does not exist -- create it with files.mkdir")
    return dst


# -- READ ------------------------------------------------------------------

@tool(
    name="files.list",
    tier=Tier.READ,
    params={
        "path": "Directory to list",
        "pattern": "Glob pattern, e.g. '*.pdf' or '**/*.py' for recursive",
        "limit": f"Maximum entries to show (cap {LIST_LIMIT})",
    },
    examples=["files.list path=. pattern=*.md"],
)
def files_list(ctx, path: str, pattern: str = "*", limit: int = 100) -> str:
    """List the files in a directory, folders first. Use this before moving or trashing anything."""
    target = ctx.check_path(path)
    if not target.exists():
        raise ToolError(f"{target} does not exist")
    if not target.is_dir():
        raise ToolError(f"{target} is a file, not a directory -- use files.read")

    limit = max(1, min(int(limit), LIST_LIMIT))
    try:
        scanned = list(itertools.islice(target.glob(pattern), SCAN_LIMIT + 1))
    except (OSError, ValueError) as exc:
        raise ToolError(f"could not list {target} with {pattern!r}: {exc}") from None
    capped = len(scanned) > SCAN_LIMIT
    entries = [e for e in scanned[:SCAN_LIMIT] if not _is_forbidden(ctx, e)]
    if not entries:
        return f"{target}: nothing matches {pattern!r}"

    entries.sort(key=lambda e: (not e.is_dir(), str(e).lower()))
    count = f"{SCAN_LIMIT}+ entries, scan stopped" if capped else f"{len(entries)} entries"
    lines = [f"{target} ({count})"]
    for item in entries[:limit]:
        name = item.relative_to(target).as_posix()
        try:
            size = "dir" if item.is_dir() else _human_size(item.stat().st_size)
        except OSError:
            size = "?"
        lines.append(f"  {size:>8}  {name}{'/' if item.is_dir() else ''}")
    if len(entries) > limit:
        lines.append(f"  ... and {len(entries) - limit} more (narrow the pattern or raise limit)")
    return "\n".join(lines)


@tool(
    name="files.read",
    tier=Tier.READ,
    params={"path": "File to read", "max_chars": f"Truncate after this many characters (cap {READ_LIMIT:,})"},
)
def files_read(ctx, path: str, max_chars: int = 4000) -> str:
    """Read a whole text file, secrets masked. For a slice of a big source file use code.read_range."""
    target = ctx.check_path(path)
    if not target.is_file():
        raise ToolError(f"{target} is not a file")

    with target.open("rb") as fh:
        head = fh.read(SNIFF_BYTES)
    if b"\x00" in head:
        raise ToolError(f"{target} looks binary ({_human_size(target.stat().st_size)}); not reading it")

    max_chars = max(1, min(int(max_chars), READ_LIMIT))
    text = target.read_text(encoding="utf-8", errors="replace")
    body = text[:max_chars]
    if len(text) > max_chars:
        body += f"\n... [truncated: showed {max_chars:,} of {len(text):,} chars]"
    # This string is about to reach the LLM.
    return ctx.scrub(body)


# -- WRITE -----------------------------------------------------------------

@tool(
    name="files.move",
    tier=Tier.WRITE,
    params={"src": "File or folder to move", "dst": "New path, or an existing folder to move it into"},
    undo="files.move with src and dst swapped (the result prints the exact call)",
)
def files_move(ctx, src: str, dst: str) -> str:
    """Move or rename a file or folder. Never overwrites an existing file."""
    source = ctx.check_path(src)
    if not source.exists():
        raise ToolError(f"{source} does not exist")
    _refuse_precious(ctx, source, "move")
    destination = _resolve_destination(source, ctx.check_path(dst))

    shutil.move(str(source), str(destination))
    ctx.log(f"moved {source} -> {destination}")
    return f"moved {source} -> {destination}\nundo: files.move src='{destination}' dst='{source}'"


@tool(
    name="files.copy",
    tier=Tier.WRITE,
    params={"src": "File or folder to copy", "dst": "New path, or an existing folder to copy it into"},
    undo="files.trash the copy printed in the result",
)
def files_copy(ctx, src: str, dst: str) -> str:
    """Copy a file or folder. Never overwrites an existing file."""
    source = ctx.check_path(src)
    if not source.exists():
        raise ToolError(f"{source} does not exist")
    destination = _resolve_destination(source, ctx.check_path(dst))

    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    ctx.log(f"copied {source} -> {destination}")
    return f"copied {source} -> {destination}\nundo: files.trash path='{destination}'"


@tool(
    name="files.mkdir",
    tier=Tier.WRITE,
    params={"path": "Folder to create (parents are created too)"},
    undo="files.trash the folder, while it is still empty",
)
def files_mkdir(ctx, path: str) -> str:
    """Create a folder, including any missing parents. Harmless if it already exists."""
    target = ctx.check_path(path)
    if target.is_dir():
        return f"{target} already exists"
    if target.exists():
        raise ToolError(f"{target} exists and is a file")
    target.mkdir(parents=True)
    ctx.log(f"created {target}")
    return f"created {target}"


# -- DANGER / restore ------------------------------------------------------

@tool(
    name="files.trash",
    tier=Tier.DANGER,
    params={"path": "File or folder to move into .servant/trash"},
    undo="files.restore item=<id printed in the result>",
)
def files_trash(ctx, path: str) -> str:
    """Move a file or folder to the agent's trash. Nothing is ever deleted outright."""
    target = ctx.check_path(path)
    if not target.exists():
        raise ToolError(f"{target} does not exist")
    _refuse_precious(ctx, target, "trash")
    trash = _trash_dir(ctx)
    if target == trash or trash in target.parents:
        raise ToolError(f"{target} is already in the trash")

    item_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    slot = trash / item_id
    slot.mkdir()
    shutil.move(str(target), str(slot / target.name))
    (trash / f"{item_id}.json").write_text(
        json.dumps({
            "id": item_id,
            "name": target.name,
            "original": str(target),
            "trashed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "dir" if (slot / target.name).is_dir() else "file",
        }, indent=2),
        encoding="utf-8",
    )
    ctx.log(f"trashed {target} as {item_id}")
    return f"trashed {target}\nid: {item_id}\nundo: files.restore item={item_id}"


@tool(
    name="files.restore",
    tier=Tier.WRITE,
    params={
        "item": "Trash id, original path, or name. Leave empty to list the trash",
        "to": "Restore somewhere else instead of the original location",
    },
    undo="files.trash the restored path",
)
def files_restore(ctx, item: str = "", to: str = "") -> str:
    """Put something back out of the trash. Call with no item to see what is in there."""
    entries = _trash_entries(ctx)
    if not item:
        if not entries:
            return "trash is empty"
        lines = [f"trash ({len(entries)} items, newest first)"]
        lines += [f"  {e['id']}  {e['kind']:4}  {e['original']}" for e in entries[:50]]
        return "\n".join(lines)

    matches = [e for e in entries if item in (e["id"], e["name"], e["original"])]
    if not matches:
        raise ToolError(f"nothing in the trash matches {item!r} -- call files.restore with no item to list it")
    if len(matches) > 1 and item not in {e["id"] for e in matches}:
        ids = ", ".join(e["id"] for e in matches)
        raise ToolError(f"{len(matches)} trashed items match {item!r}; pass one id: {ids}")
    entry = matches[0]

    trash = _trash_dir(ctx)
    stored = trash / entry["id"] / entry["name"]
    if not stored.exists():
        raise ToolError(f"trash record {entry['id']} exists but its content is gone")

    destination = ctx.check_path(to or entry["original"])
    if to and destination.is_dir():
        destination = destination / entry["name"]
    if destination.exists():
        raise ToolError(f"{destination} is occupied -- pass to=<another path>")
    if not destination.parent.is_dir():
        raise ToolError(f"{destination.parent} no longer exists -- recreate it or pass to=")

    shutil.move(str(stored), str(destination))
    (trash / entry["id"]).rmdir()
    (trash / f"{entry['id']}.json").unlink()
    ctx.log(f"restored {entry['id']} -> {destination}")
    return f"restored {destination}"
