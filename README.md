# FolderCleanup

A small, dependency-free Python CLI that tidies a folder. **Dry run by default**; nothing changes without `--apply`.

```
python3 foldercleanup.py ~/Downloads --sort --duplicates --empty-dirs        # preview
python3 foldercleanup.py ~/Downloads --sort --duplicates --empty-dirs --apply
```

| Flag | Effect |
|------|--------|
| `--sort` | Move top-level files into `Images/`, `Documents/`, `Audio/`, `Video/`, `Archives/`, `Code/`, `Installers/`, `Other/` |
| `--duplicates` | Delete byte-identical duplicates (recursive, keeps the first copy) |
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
