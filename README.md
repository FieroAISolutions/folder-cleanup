# FolderCleanup

A small, dependency-free Python CLI that tidies a folder. **Dry run by default**; nothing changes without `--apply`.

## Web UI

```
python3 webui.py          # opens http://127.0.0.1:8765/
```

Pick a folder, tick actions, set filters, **Preview** the plan, then **Apply**. It listens on localhost only, and every POST needs a per-run token. Apply uses the saved preview, verifies the folder still matches, and rejects changes instead of silently changing the plan. Previews expire after 30 minutes, are single use, and disappear when the server restarts. Only the most recent 32 previews are retained.

On Windows: `python webui.py`. After a successful cleanup, **Undo last cleanup** restores moved files and recovered duplicates. Recovery data stays in `.foldercleanup-recovery` inside the selected folder; duplicates are moved there, not permanently deleted. This does **not** free disk space. Keep that folder until you no longer need recovery.

Each cleanup writes a journal before moving files. If a locked file or another error interrupts the cleanup, the error identifies the journal and earlier changes remain recoverable. To undo after restarting, use its session folder name:

```
python foldercleanup.py "C:\Users\you\Downloads" --undo SESSION_ID
```

Undo refuses to overwrite existing files or restore files modified after cleanup. Resolve the reported conflict and retry. It only removes newly created category folders if they are empty. Recovery stays on the same disk and is not a backup against disk failure.

Hidden/system files, dot folders, version-control folders, dependency folders, symlinks, and Windows reparse points (including junctions) are excluded. Linked destination folders, blank paths, drive roots, and standard Windows system locations are rejected. Some OneDrive files marked as reparse points may therefore be skipped. Empty-folder removal is limited to folders explicitly listed in the preview; newly emptied parents require a new preview.

Preview validation hashes eligible files, which can take time on large folders. Avoid editing the folder while cleanup or undo runs: validation detects changes before execution and checks each source again, but cannot lock out other programs for the entire operation.

## Command line

```
python3 foldercleanup.py ~/Downloads --sort --duplicates --empty-dirs        # preview
python3 foldercleanup.py ~/Downloads --sort --duplicates --empty-dirs --apply
```

| Flag | Effect |
|------|--------|
| `--sort` | Move top-level files into `Images/`, `Documents/`, `Audio/`, `Video/`, `Archives/`, `Code/`, `Installers/`, `Other/` |
| `--duplicates` | Move byte-identical duplicates to recovery storage (recursive, keeps the first copy) |
| `--old DAYS` | Move top-level files not modified in DAYS days to `_Old/` |
| `--empty-dirs` | Remove empty directories |
| `--apply` | Actually perform the actions |

### Filters

Filters limit which files `--sort`, `--duplicates` and `--old` touch (they don't affect `--empty-dirs`):

| Flag | Effect |
|------|--------|
| `--include GLOB` | Only files matching the pattern (repeatable), e.g. `--include '*.pdf'` |
| `--exclude GLOB` | Skip files matching the pattern (repeatable) |
| `--ext EXT` | Only files with this extension (repeatable) |
| `--min-size SIZE` / `--max-size SIZE` | Size bounds, e.g. `500KB`, `10MB`, `1GB` |

Example: `python3 foldercleanup.py ~/Downloads --sort --ext pdf --ext docx --min-size 1MB`

Name collisions are resolved by renaming (`file (1).txt`), never overwriting. Hidden files are left alone.

Tests: `pip install pytest && pytest`
