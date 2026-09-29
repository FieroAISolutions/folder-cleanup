"""Filesystem boundaries, preview snapshots, and recoverable cleanup sessions."""
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

RECOVERY = '.foldercleanup-recovery'
PROTECTED = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv',
             'venv', '$recycle.bin', 'system volume information', RECOVERY}


def blocked(path):
    info = path.lstat()
    attrs = getattr(info, 'st_file_attributes', 0)
    return (path.name.startswith('.') or path.name.lower() in PROTECTED
            or stat.S_ISLNK(info.st_mode)
            or bool(attrs & (0x2 | 0x4 | 0x400)))  # hidden, system, reparse point


def validate_root(value):
    if not str(value).strip():
        raise ValueError('Choose a folder first.')
    raw = Path(os.path.abspath(Path(value).expanduser()))
    if not raw.is_dir():
        raise ValueError(f'Not an existing directory: {raw}')
    for part in [raw, *raw.parents]:
        if part.parent != part and blocked(part):
            raise ValueError(f'Protected folder or linked path: {part}')
    root = raw.resolve()
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError('Choose an existing folder, not a drive root.')
    for env in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData'):
        base = os.environ.get(env)
        if base and root.is_relative_to(Path(base).resolve()):
            raise ValueError(f'Protected system folder: {root}')
    return root


def walk(root):
    """Prune protected directories before descending; never follow links."""
    for path in sorted(root.iterdir()):
        if blocked(path):
            continue
        yield path
        if path.is_dir():
            yield from walk(path)


def fingerprint(path):
    info = path.stat()
    if path.is_dir():
        return ['dir', info.st_dev, info.st_ino]
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f'File changed while scanning: {path}')
    return ['file', info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, digest.hexdigest()]


def snapshot(root):
    return {str(p.relative_to(root)): fingerprint(p) for p in [root, *walk(root)]}


def check_path(root, path, recovery=False):
    if '..' in path.parts or not path.is_relative_to(root) or path == root:
        raise ValueError(f'Path outside cleanup folder: {path}')
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.exists() or part.is_symlink():
            if recovery and part == root / RECOVERY:
                attrs = getattr(part.lstat(), 'st_file_attributes', 0)
                if part.is_symlink() or attrs & 0x400:
                    raise ValueError('Recovery folder must not be a link.')
            elif blocked(part):
                raise ValueError(f'Protected or linked path: {part}')


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temp.replace(path)


def execute(plan, root, expected=None, report=None):
    import shutil
    root = validate_root(root)
    current = snapshot(root)
    if expected is not None and current != expected:
        raise ValueError('Folder changed since preview. Preview again before applying.')
    for kind, src, dest in plan:
        check_path(root, src)
        if dest is not None:
            check_path(root, dest)
        if kind not in ('move', 'delete', 'rmdir'):
            raise ValueError('Unknown cleanup action.')
    if not plan:
        return []
    session = root / RECOVERY / uuid.uuid4().hex
    check_path(root, session, recovery=True)
    session.mkdir(parents=True)
    journal = session / 'journal.json'
    data = {'root': str(root), 'actions': [], 'created_dirs': [], 'state': 'running'}
    save(journal, data)
    if report is not None:
        report['session_id'] = session.name
    done = []
    try:
        for kind, src, dest in plan:
            check_path(root, src)
            if fingerprint(src) != current[str(src.relative_to(root))]:
                raise ValueError(f'File changed during cleanup: {src}')
            target = session / f'{len(done):08d}.saved' if kind == 'delete' else dest
            if kind == 'delete':
                check_path(root, dest)
                if fingerprint(src)[-1] != fingerprint(dest)[-1]:
                    raise ValueError(f'Duplicate changed during cleanup: {src}')
            if target is not None:
                check_path(root, target, recovery=kind == 'delete')
                if target.exists():
                    raise ValueError(f'Destination now exists: {target}. Preview again.')
                missing = []
                parent = target.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for parent in reversed(missing):
                    data['created_dirs'].append(str(parent.relative_to(root)))
                    save(journal, data)
                    parent.mkdir()
            entry = {'kind': kind, 'src': str(src.relative_to(root)),
                     'dest': str(target.relative_to(root)) if target else None,
                     'before': current[str(src.relative_to(root))], 'state': 'pending'}
            data['actions'].append(entry)
            save(journal, data)
            if kind == 'rmdir':
                src.rmdir()
            else:
                shutil.move(str(src), str(target))
            entry['state'] = 'done'
            save(journal, data)
            done.append((kind, src, dest))
        data['state'] = 'complete'
    except Exception as exc:
        data['state'] = 'interrupted'
        data['error'] = str(exc)
        raise ValueError(f'{exc} Completed {len(done)} actions. Recovery journal: {journal}') from exc
    finally:
        save(journal, data)
    return done


def undo(root, session_id):
    import shutil
    root = validate_root(root)
    if not isinstance(session_id, str) or len(session_id) != 32 or any(c not in '0123456789abcdef' for c in session_id):
        raise ValueError('Invalid recovery session ID.')
    journal = root / RECOVERY / session_id / 'journal.json'
    check_path(root, journal, recovery=True)
    data = json.loads(journal.read_text(encoding='utf-8'))
    if data['root'] != str(root):
        raise ValueError('Recovery journal belongs to another folder.')
    for entry in reversed(data['actions']):
        if entry['state'] == 'undone':
            continue
        src = root / entry['src']
        check_path(root, src)
        if entry['kind'] == 'rmdir':
            if not src.exists():
                src.mkdir()
            elif not src.is_dir():
                raise ValueError(f'Restore location occupied: {src}')
        else:
            dest = root / entry['dest']
            check_path(root, dest, recovery=True)
            if src.exists() and not dest.exists() and fingerprint(src) == entry['before']:
                pass  # operation never ran, or an interrupted undo already restored it
            elif src.exists():
                raise ValueError(f'Restore location occupied: {src}')
            elif not dest.exists() or fingerprint(dest) != entry['before']:
                raise ValueError(f'Recovery file missing or changed: {dest}')
            else:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
        entry['state'] = 'undone'
        save(journal, data)
    for name in reversed(data['created_dirs']):
        path = root / name
        check_path(root, path)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    data['state'] = 'undone'
    save(journal, data)
    return session_id
