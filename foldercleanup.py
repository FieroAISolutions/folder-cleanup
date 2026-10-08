#!/usr/bin/env python3
"""FolderCleanup: tidy a folder by sorting files, removing duplicates and empty dirs.

Safe by default: nothing is changed unless --apply is given.
"""
import argparse
import fnmatch
import hashlib
import re
import shutil
import sys
import time
from pathlib import Path
import safety

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "Video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".html", ".css", ".json", ".sh"},
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk"},
}
OTHER = "Other"


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return OTHER


def unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 1
    while True:
        cand = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            safety.checkpoint()
            h.update(chunk)
    return h.hexdigest()


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg]?)b?\s*", text.lower())
    if not m:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r} (try 500KB, 10MB, 1GB)")
    return int(float(m.group(1)) * {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}[m.group(2)])


def make_filter(include=(), exclude=(), exts=(), min_size=None, max_size=None):
    """Build a predicate deciding whether a file is eligible for any action."""
    exts = {("." + e.lstrip(".")).lower() for e in exts}

    def match(path: Path) -> bool:
        name = path.name
        if include and not any(fnmatch.fnmatch(name, g) for g in include):
            return False
        if any(fnmatch.fnmatch(name, g) for g in exclude):
            return False
        if exts and path.suffix.lower() not in exts:
            return False
        if min_size is not None or max_size is not None:
            size = path.stat().st_size
            if min_size is not None and size < min_size:
                return False
            if max_size is not None and size > max_size:
                return False
        return True

    return match


def top_level_files(root: Path, ok=lambda p: True):
    return sorted(p for p in root.iterdir()
                  if not safety.blocked(p) and p.is_file() and ok(p))


def plan_sort(root: Path, ok=lambda p: True):
    """Return [(src, dest)] moving top-level files into category folders."""
    return [(p, root / category_for(p) / p.name) for p in top_level_files(root, ok)]


def plan_duplicates(root: Path, ok=lambda p: True, keepers=()):
    """Return duplicate files (later copies of identical content), scanning recursively."""
    by_size, dupes = {}, []
    requested = set(keepers)
    matched = set()
    for p in safety.walk(root):
        if p.is_file() and not p.is_symlink() and ok(p):
            by_size.setdefault(p.stat().st_size, []).append(p)
    for group in by_size.values():
        if len(group) < 2:
            continue
        seen = {}
        for p in group:
            safety.checkpoint('Comparing duplicates', None, None, p)
            h = file_hash(p)
            seen.setdefault(h, []).append(p)
        for identical in seen.values():
            if len(identical) < 2:
                continue
            chosen = [p for p in identical if str(p.relative_to(root)) in requested]
            if len(chosen) > 1:
                raise ValueError('Choose exactly one keeper per duplicate group.')
            keeper = chosen[0] if chosen else identical[0]
            if chosen:
                matched.add(str(keeper.relative_to(root)))
            dupes.extend((p, keeper) for p in identical if p != keeper)
    if requested != matched:
        raise ValueError('A selected duplicate keeper is no longer in a matching group. Clear keeper choices and preview again.')
    return dupes


def plan_old(root: Path, days: int, ok=lambda p: True):
    cutoff = time.time() - days * 86400
    return [p for p in top_level_files(root, ok) if p.stat().st_mtime < cutoff]


def empty_dirs(root: Path):
    """Empty directories, deepest first (so parents emptied by removal are included by caller loop)."""
    return [d for d in sorted(safety.walk(root), key=lambda p: len(p.parts), reverse=True)
            if d.is_dir() and not d.is_symlink() and not any(d.iterdir())]


def build_plan(root: Path, *, sort=False, duplicates=False, empty_dirs_=False, old=None, ok=lambda p: True, keepers=()):
    """Compute actions as (kind, src, dest) tuples; kind is 'move', 'delete' or 'rmdir'.

    For 'delete', dest is the file it duplicates. Nothing is changed on disk.
    """
    root = safety.validate_root(root)
    if old is not None and (isinstance(old, bool) or not isinstance(old, int) or old < 0):
        raise ValueError('Age must be a nonnegative whole number of days.')
    plan, used, taken = [], set(), set()

    def claim(dest: Path) -> Path:
        dest = unique_dest(dest)
        while dest in taken:
            dest = unique_dest(dest.with_name(dest.name + "_"))
        taken.add(dest)
        return dest

    if duplicates:
        for dup, orig in plan_duplicates(root, ok, keepers):
            plan.append(("delete", dup, orig))
            used.add(dup)
    if old is not None:
        for p in plan_old(root, old, ok):
            if p not in used:
                plan.append(("move", p, claim(root / "_Old" / p.name)))
                used.add(p)
    if sort:
        for src, dest in plan_sort(root, ok):
            if src not in used:
                plan.append(("move", src, claim(dest)))
    if empty_dirs_:
        destinations = [dest for kind, _, dest in plan if kind == 'move']
        plan += [("rmdir", d, None) for d in empty_dirs(root)
                 if not any(dest.is_relative_to(d) for dest in destinations)]
    for _, src, dest in plan:
        safety.check_path(root, src)
        if dest is not None:
            safety.check_path(root, dest)
    return plan


def apply_plan(plan, root: Path, expected=None, report=None):
    """Execute a plan from build_plan. Returns a list of (kind, src, dest) actually done."""
    return safety.execute(plan, root, expected, report)


def describe(kind, src: Path, dest, root: Path) -> str:
    rel = lambda p: p.relative_to(root)
    if kind == "delete":
        return f"recoverable removal {rel(src)} (duplicate of {rel(dest)})"
    if kind == "move":
        return f"move   {rel(src)} -> {rel(dest)}"
    return f"rmdir  {rel(src)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="folder to clean")
    ap.add_argument("--undo", metavar="SESSION_ID", help="restore a cleanup session")
    ap.add_argument("--apply", action="store_true", help="actually make changes (default: dry run)")
    ap.add_argument("--sort", action="store_true", help="move top-level files into category folders")
    ap.add_argument("--duplicates", action="store_true", help="delete duplicate files (keeps first copy)")
    ap.add_argument("--empty-dirs", action="store_true", help="remove empty directories")
    ap.add_argument("--old", type=int, metavar="DAYS", help="move top-level files older than DAYS to _Old")
    flt = ap.add_argument_group("filters", "restrict which files --sort, --duplicates and --old touch")
    flt.add_argument("--include", action="append", default=[], metavar="GLOB", help="only files matching GLOB (repeatable), e.g. '*.pdf'")
    flt.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip files matching GLOB (repeatable)")
    flt.add_argument("--ext", action="append", default=[], metavar="EXT", help="only files with this extension (repeatable), e.g. jpg")
    flt.add_argument("--min-size", type=parse_size, metavar="SIZE", help="only files at least SIZE, e.g. 10MB")
    flt.add_argument("--max-size", type=parse_size, metavar="SIZE", help="only files at most SIZE")
    args = ap.parse_args(argv)
    ok = make_filter(args.include, args.exclude, args.ext, args.min_size, args.max_size)

    root = safety.validate_root(args.folder)
    if args.undo:
        safety.undo(root, args.undo)
        print(f'Restored session {args.undo}.')
        return 0
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if not (args.sort or args.duplicates or args.empty_dirs or args.old is not None):
        ap.error("choose at least one of --sort, --duplicates, --empty-dirs, --old")

    expected = safety.snapshot(root)
    plan = build_plan(root, sort=args.sort, duplicates=args.duplicates,
                      empty_dirs_=args.empty_dirs, old=args.old, ok=ok)
    tag = "" if args.apply else "[dry-run] "
    actions = apply_plan(plan, root, expected) if args.apply else plan
    for a in actions:
        print(tag + describe(*a, root))
    print(f"\n{len(actions)} action(s) {'applied' if args.apply else 'planned (re-run with --apply)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
