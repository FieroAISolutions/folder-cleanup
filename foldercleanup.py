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
                  if p.is_file() and not p.name.startswith(".") and ok(p))


def plan_sort(root: Path, ok=lambda p: True):
    """Return [(src, dest)] moving top-level files into category folders."""
    return [(p, root / category_for(p) / p.name) for p in top_level_files(root, ok)]


def plan_duplicates(root: Path, ok=lambda p: True):
    """Return duplicate files (later copies of identical content), scanning recursively."""
    by_size, dupes = {}, []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink() and ok(p):
            by_size.setdefault(p.stat().st_size, []).append(p)
    for group in by_size.values():
        if len(group) < 2:
            continue
        seen = {}
        for p in group:
            h = file_hash(p)
            if h in seen:
                dupes.append((p, seen[h]))
            else:
                seen[h] = p
    return dupes


def plan_old(root: Path, days: int, ok=lambda p: True):
    cutoff = time.time() - days * 86400
    return [p for p in top_level_files(root, ok) if p.stat().st_mtime < cutoff]


def empty_dirs(root: Path):
    """Empty directories, deepest first (so parents emptied by removal are included by caller loop)."""
    return [d for d in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
            if d.is_dir() and not d.is_symlink() and not any(d.iterdir())]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path, help="folder to clean")
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

    root = args.folder.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if not (args.sort or args.duplicates or args.empty_dirs or args.old is not None):
        ap.error("choose at least one of --sort, --duplicates, --empty-dirs, --old")

    tag = "" if args.apply else "[dry-run] "
    count = 0

    def move(src, dest):
        nonlocal count
        dest = unique_dest(dest)
        print(f"{tag}move   {src.relative_to(root)} -> {dest.relative_to(root)}")
        if args.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        count += 1

    # Order matters: dedupe first so we don't sort files we're about to delete.
    if args.duplicates:
        for dup, orig in plan_duplicates(root, ok):
            print(f"{tag}delete {dup.relative_to(root)} (duplicate of {orig.relative_to(root)})")
            if args.apply:
                dup.unlink()
            count += 1
    if args.old is not None:
        for p in plan_old(root, args.old, ok):
            if p.exists():
                move(p, root / "_Old" / p.name)
    if args.sort:
        for src, dest in plan_sort(root, ok):
            if src.exists():
                move(src, dest)
    if args.empty_dirs:
        if args.apply:
            removed = True
            while removed:
                removed = False
                for d in empty_dirs(root):
                    print(f"rmdir  {d.relative_to(root)}")
                    d.rmdir()
                    count += 1
                    removed = True
        else:
            for d in empty_dirs(root):
                print(f"{tag}rmdir  {d.relative_to(root)}")
                count += 1

    print(f"\n{count} action(s) {'applied' if args.apply else 'planned (re-run with --apply)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
