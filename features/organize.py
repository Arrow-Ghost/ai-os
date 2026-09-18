"""
organize.* -- tidy a messy folder into subfolders, in two steps.

    organize.plan   READ    scan, and write down what WOULD move. Changes nothing.
    organize.apply  DANGER  do exactly that plan, after a human has said yes

The split is the point: the human reviews a concrete table, not an intention.
`apply` refuses to improvise -- a file that changed or vanished since the plan
was made is skipped, not guessed at -- and every move it makes is written to
an undo manifest.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

from ._util import stop_requested

MAX_FILES = 2000
TABLE_ROWS = 60

CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tif", ".tiff", ".ico"},
    "Documents": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages", ".epub"},
    "Spreadsheets": {".xls", ".xlsx", ".ods", ".csv", ".tsv", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"},
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".appimage"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb",
             ".php", ".html", ".css", ".json", ".yaml", ".yml", ".ipynb", ".sh", ".ps1", ".sql"},
}
EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
MODES = ("type", "date", "extension")


def _plans_dir(ctx) -> Path:
    return ctx.state_dir("organize")


def _bucket(path: Path, stat, by: str) -> str:
    ext = path.suffix.lower()
    if by == "type":
        return EXT_TO_CATEGORY.get(ext, "Other")
    if by == "extension":
        return ext.lstrip(".").upper() or "NO_EXTENSION"
    return datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m")


@tool(
    name="organize.plan",
    tier=Tier.READ,
    params={
        "path": "Folder to tidy (only its top-level files are considered)",
        "by": "How to group: 'type' (Images, Documents...), 'date' (YYYY-MM), or 'extension'",
        "include_hidden": "Also move dotfiles",
    },
    examples=["organize.plan path=~/Downloads by=type"],
)
def organize_plan(ctx, path: str, by: str = "type", include_hidden: bool = False) -> str:
    """Scan a folder and propose moving its loose files into subfolders. A dry run: nothing changes.

    Show the table to the user, then call organize.apply with the plan id if they want it done.
    """
    folder = ctx.check_path(path)
    if not folder.is_dir():
        raise ToolError(f"{folder} is not a directory")
    by = by.lower().strip()
    if by not in MODES:
        raise ToolError(f"by must be one of {', '.join(MODES)}")

    moves, skipped, claimed = [], [], set()
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    if len(files) > MAX_FILES:
        raise ToolError(f"{folder} has {len(files)} files; plans are capped at {MAX_FILES}. Pick a subfolder.")

    for f in files:
        if f.name.startswith(".") and not include_hidden:
            continue
        stat = f.stat()
        bucket = _bucket(f, stat, by)
        dst = folder / bucket / f.name
        if dst.exists() or str(dst).lower() in claimed:
            skipped.append((f.name, f"{bucket}/{f.name} already exists"))
            continue
        claimed.add(str(dst).lower())
        moves.append({"src": str(f), "dst": str(dst), "size": stat.st_size, "mtime": stat.st_mtime})

    if not moves:
        note = f" ({len(skipped)} skipped)" if skipped else ""
        return f"{folder}: nothing to organize{note}"

    plan_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    (_plans_dir(ctx) / f"{plan_id}.json").write_text(
        json.dumps({"id": plan_id, "folder": str(folder), "by": by, "moves": moves, "applied": None}, indent=2),
        encoding="utf-8",
    )

    counts: dict[str, int] = {}
    for m in moves:
        counts[Path(m["dst"]).parent.name] = counts.get(Path(m["dst"]).parent.name, 0) + 1

    lines = [
        f"DRY RUN -- nothing has moved. plan id: {plan_id}",
        f"folder: {folder}   group by: {by}   files to move: {len(moves)}",
        "",
        "  " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
        "",
        f"  {'FILE':<48} -> TO",
    ]
    for m in moves[:TABLE_ROWS]:
        name = Path(m["src"]).name
        lines.append(f"  {name[:48]:<48} -> {Path(m['dst']).parent.name}/")
    if len(moves) > TABLE_ROWS:
        lines.append(f"  ... and {len(moves) - TABLE_ROWS} more")
    if skipped:
        lines.append(f"\nskipped {len(skipped)}: " + "; ".join(f"{n} ({why})" for n, why in skipped[:10]))
    lines.append(f"\nto do it: organize.apply plan_id={plan_id}")
    return "\n".join(lines)


@tool(
    name="organize.apply",
    tier=Tier.DANGER,
    params={"plan_id": "The id printed by organize.plan"},
    undo="each move is reversed by the .undo.json manifest named in the result (files.move dst->src)",
)
def organize_apply(ctx, plan_id: str) -> str:
    """Carry out a plan made by organize.plan. Only after the user has seen the plan and agreed."""
    plan_file = _plans_dir(ctx) / f"{Path(plan_id).name}.json"
    if not plan_file.exists():
        raise ToolError(f"no plan {plan_id!r} -- run organize.plan first")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if plan.get("applied"):
        raise ToolError(f"plan {plan_id} was already applied at {plan['applied']}; make a new plan")

    folder = ctx.check_path(plan["folder"])
    undo_file = _plans_dir(ctx) / f"{plan['id']}.undo.json"
    done, skipped, stopped = [], [], False

    def save_progress() -> None:
        undo_file.write_text(json.dumps([{"src": d, "dst": s} for s, d in done], indent=2), encoding="utf-8")

    for m in plan["moves"]:
        if stop_requested(ctx):
            stopped = True
            break
        src, dst = Path(m["src"]), Path(m["dst"])
        if folder not in src.parents or folder not in dst.parents:
            skipped.append((src.name, "outside the planned folder"))
            continue
        try:
            stat = src.stat()
        except FileNotFoundError:
            skipped.append((src.name, "gone since the plan"))
            continue
        if stat.st_size != m["size"] or abs(stat.st_mtime - m["mtime"]) > 1:
            skipped.append((src.name, "changed since the plan"))
            continue
        if dst.exists():
            skipped.append((src.name, "destination now exists"))
            continue
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dst))
        done.append((str(src), str(dst)))
        if len(done) % 25 == 0:
            save_progress()

    save_progress()
    plan["applied"] = time.strftime("%Y-%m-%d %H:%M:%S")
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    ctx.log(f"organize {plan['id']}: moved {len(done)}, skipped {len(skipped)}")

    lines = [f"moved {len(done)} of {len(plan['moves'])} files in {folder}"]
    if skipped:
        lines.append(f"skipped {len(skipped)}: " + "; ".join(f"{n} ({why})" for n, why in skipped[:10]))
    lines.append(f"undo manifest: {undo_file}")
    if stopped:
        raise ToolError("stopped by the kill switch part-way. " + " ".join(lines))
    return "\n".join(lines)
