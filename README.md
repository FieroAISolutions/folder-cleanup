# FolderCleanup

A small, dependency-free Python CLI that tidies a folder. **Dry run by default**; nothing changes without `--apply`.

## Web UI

```
python3 webui.py          # opens http://127.0.0.1:8765/
```

Pick a folder, tick actions, set filters, **Preview** the plan, then **Apply**. It listens on localhost only, and every POST needs a per-run token. Apply uses the saved preview, verifies the folder still matches, and rejects changes instead of silently changing the plan. Previews expire after 30 minutes, are single use, and disappear when the server restarts. Only the most recent 32 previews are retained.

On Windows: `python webui.py`. After a successful cleanup, **Undo last cleanup** restores moved files and recovered duplicates. Recovery data stays in `.foldercleanup-recovery` inside the selected folder; duplicates are moved there, not permanently deleted. This does **not** free disk space. Keep that folder until you no longer need recovery.

### Review and control a cleanup

- On Windows, double-click **Start FolderCleanup.cmd** (Python must be installed). Keep its console open while using the app; closing it stops the server.
- Use **Browse for folder**, or the **Downloads** and **Desktop** shortcuts. Windows shortcuts follow your configured folder locations, including redirected Desktop locations. If the native picker is unavailable, paste a folder path.
- Duplicate groups show paths, sizes, and modification dates. Choose a keeper in each group, then **Update preview with keeper choices**. Keeper choices are checked against the current duplicate groups; stale choices require a fresh selection. Other copies are eligible for recovery. A keeper can still be moved by a selected sort/archive action, as shown in the plan.
- Uncheck any individual action, or use **Select all / Select none**. Only selected actions from that saved preview are applied. The summary shows file counts and potential reclaimable duplicate bytes, with zero space freed while recovery copies remain.
- Scans and cleanup run in the background. Progress shows the current phase and path, plus action counts during cleanup. **Cancel** is checked between files and during hashing; an in-flight filesystem move may finish before cancellation takes effect.
- A locked file stops the cleanup and reports the error, completed actions, and recovery session. **Undo last cleanup** is available after partial failures and cancellations as well as successful cleanups.
- **Activity history** persists across restarts and provides restore buttons. It records running recovery sessions before execution so an interrupted server run remains discoverable. Logs are stored at `%LOCALAPPDATA%\FolderCleanup\activity.jsonl` on Windows (or `~/.local/state/FolderCleanup/activity.jsonl` elsewhere). Logs contain folder paths; recovery journals remain inside each cleaned folder.

The browser reconnects to its running task after a page reload. After a server restart, reload the page to obtain its new session token and check activity history. One background task runs at a time. The synchronous API endpoints remain available for compatibility; use the background endpoints for progress and cancellation.

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

Tests: install pytest with `python -m pip install pytest`, then run
`python -m pytest -q --basetemp=pytest-tmp`. This matches CI and keeps Windows
test fixtures outside AppData, which the cleanup safety policy blocks.
Pytest clears `pytest-tmp` before running; reserve that directory for tests.

Optional browser integration test: install `playwright` and Microsoft Edge, then run `pytest tests/test_browser.py`. This test uses temporary files and a mocked folder picker; it never cleans your personal folders.

## Community and security

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Please report
security issues privately as described in [SECURITY.md](SECURITY.md). This
project is available under the [MIT License](LICENSE).
