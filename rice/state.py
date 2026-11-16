"""State file, whole-file installer and live-patch writer (PLAN §2, T3)."""
import difflib
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

RICE_KDL = ".config/niri/cfg/rice.kdl"


@dataclass
class Change:  # minimal local copy of the §3 contract; kind also uses "modified" here
    relpath: str
    kind: str  # new|changed|binary|run|modified
    diff: str | None = None


@dataclass
class Status:
    enabled: bool = False
    variant: str | None = None
    needs_apply: bool | None = False
    stale_sources: list = field(default_factory=list)
    recipe_changed: bool = False
    modified: list = field(default_factory=list)
    missing_packages: list = field(default_factory=list)
    app_missing: bool = False
    update_available: bool | None = None


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "niri-noctalia-rice" / "state.json"


def load(path: Path | None = None) -> dict:
    p = path or state_path()
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return {}


def save(state: dict, path: Path | None = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, p)


def is_enabled(state: dict, cid: str) -> bool:
    return cid in state and not state[cid].get("disabled")


def disabled_entry(entry: dict) -> dict:
    """Turn-off entry: keeps whole/seeded/patched so owned() still covers them."""
    out = {k: entry[k] for k in ("whole", "seeded", "patched") if k in entry}
    out["disabled"] = True
    return out


def owned(state: dict) -> list[str]:
    paths = set()
    for e in state.values():
        paths |= set(e.get("whole", {})) | set(e.get("seeded", [])) | set(e.get("patched", []))
    if state:
        paths.add(RICE_KDL)
    return sorted(paths)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unified(old: str, new: str, relpath: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        f"a/{relpath}", f"b/{relpath}"))


def _diff(old: bytes | None, new: bytes, relpath: str) -> tuple[str, str | None]:
    kind = "new" if old is None else "changed"
    try:
        o = "" if old is None else old.decode()
        return kind, unified(o, new.decode(), relpath)
    except UnicodeDecodeError:
        return "binary", None


def _write(target: Path, data: bytes, ts: str, src_mode_from: Path | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        bak = target.with_name(target.name + ".bak-" + ts)
        shutil.copy2(target, bak)
    tmp = target.with_name(target.name + ".rice-tmp")
    tmp.write_bytes(data)
    shutil.copymode(src_mode_from or (target if target.exists() else tmp), tmp)
    os.replace(tmp, target)


def install_whole(files: dict, home, recorded: dict, ts: str, force: bool = False,
                  reseed: bool = False, dry_run: bool = False) -> list[Change]:
    """files: {relpath: (staged_path, track)}; recorded: {relpath: sha256}.
    Seeds (track=False) are written only if missing, or when reseed (variant changed)."""
    home = Path(home)
    changes = []
    for rel, (src, track) in sorted(files.items()):
        src, live = Path(src), home / rel
        new = src.read_bytes()
        old = live.read_bytes() if live.exists() else None
        if old == new:
            continue
        if old is not None and not track and not reseed:
            continue
        if old is not None and track and not force:
            rec = recorded.get(rel)
            if rec is not None and hashlib.sha256(old).hexdigest() != rec:
                changes.append(Change(rel, "modified", _diff(old, new, rel)[1]))
                continue
        kind, diff = _diff(old, new, rel)
        changes.append(Change(rel, kind, diff))
        if not dry_run:
            _write(live, new, ts, src)
    return changes


def write_patched(relpath: str, new_text: str, home, ts: str, dry_run: bool = False) -> Change | None:
    live = Path(home) / relpath
    old = live.read_bytes() if live.exists() else None
    if old is not None and old.decode() == new_text:
        return None
    kind, diff = _diff(old, new_text.encode(), relpath)
    if not dry_run:
        _write(live, new_text.encode(), ts)
    return Change(relpath, kind, diff)
