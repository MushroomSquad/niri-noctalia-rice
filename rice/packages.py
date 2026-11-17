"""Live package-set detection and inline, TTY-inheriting package operations."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "mokka/packages.toml"
INSTALL_SH = REPO / "install.sh"
DB_LOCK = Path("/var/lib/pacman/db.lck")
GROUPS = ("base", "theme", "apps", "dev")
_COMPUTED = object()


@dataclass
class PkgSet:
    id: str
    title: str
    description: str = ""
    group: str = "apps"
    default: bool = False
    repo: list[str] = field(default_factory=list)
    aur: list[str] = field(default_factory=list)
    any_of: list[list[str]] = field(default_factory=list)
    step: str | None = None
    installed_if: dict = field(default_factory=dict)
    cmds: dict = field(default_factory=dict)  # package -> command that also satisfies it (cargo, ~/.local/bin)
    removable: bool = False
    step_always: bool = False


@dataclass
class SetStatus:
    state: str
    missing: list[str]


@dataclass
class InstallResult:
    rc: int
    failed: list[str]
    message: str


def load(path=None) -> list[PkgSet]:
    """Read validated sets in manifest order; None uses the current MANIFEST."""
    path = Path(MANIFEST if path is None else path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    rows = data.get("set", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: set: expected array of tables")
    result, seen = [], set()
    for i, row in enumerate(rows):
        def error(key, message):
            raise ValueError(f"{path}: set[{i}].{key}: {message}")
        if not isinstance(row, dict):
            error("id", "expected table")
        for key in ("id", "title", "group"):
            if key not in row:
                error(key, "required")
            if not isinstance(row[key], str) or not row[key]:
                error(key, "expected nonempty string")
        if not re.fullmatch(r"[a-z0-9-]+", row["id"]):
            error("id", "expected [a-z0-9-]+")
        if row["id"] in seen:
            error("id", "duplicate id")
        seen.add(row["id"])
        if row["group"] not in GROUPS:
            error("group", f"expected one of {GROUPS}")
        for key in ("default", "removable", "step_always"):
            if key in row and not isinstance(row[key], bool):
                error(key, "expected boolean")
        for key in ("description", "step"):
            if key in row and (not isinstance(row[key], str) or not row[key]):
                error(key, "expected nonempty string")
        for key in ("repo", "aur"):
            values = row.get(key, [])
            if not isinstance(values, list) or any(not isinstance(x, str) or not x or x.startswith("-") for x in values):
                error(key, "expected array of package names")
        alternatives = row.get("any_of", [])
        if not isinstance(alternatives, list) or any(
            not isinstance(g, list) or not g or any(not isinstance(x, str) or not x or x.startswith("-") for x in g)
            for g in alternatives
        ):
            error("any_of", "expected arrays of package names")
        checks = row.get("installed_if", {})
        if not isinstance(checks, dict):
            error("installed_if", "expected table")
        for key, value in checks.items():
            if key not in ("cmd", "file", "unit"):
                error(f"installed_if.{key}", "unknown check")
            if isinstance(value, str):
                value = checks[key] = [value]
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
                error(f"installed_if.{key}", "expected nonempty string or array of strings")
        cmds = row.get("cmds", {})
        if not isinstance(cmds, dict) or any(not isinstance(v, str) or not v for v in cmds.values()):
            error("cmds", "expected table of package = command")
        if row.get("removable") and (row["group"] not in ("apps", "dev") or row.get("step")):
            error("removable", "only apps/dev sets without a step may be removed")
        if row.get("step_always") and not row.get("step"):
            error("step_always", "requires step")
        result.append(PkgSet(**row))
    return result


def _unique(names):
    return list(dict.fromkeys(names))


def _names(sets):
    return _unique(n for s in sets for n in s.repo + s.aur + [n for g in s.any_of for n in g])


def unsatisfied(names: Iterable[str]) -> set[str] | None:
    """One dependency test, including pacman's provides resolution."""
    try:
        p = subprocess.run(["pacman", "-T", *_unique(names)], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if p.returncode == 0:
        return set()
    if p.returncode == 127:
        return set(p.stdout.splitlines())
    raise RuntimeError(f"pacman -T exited {p.returncode}: {p.stderr.strip()}")


def _which(cmd):
    extra = [str(Path(d).expanduser()) for d in ("~/.local/bin", "~/.cargo/bin")]
    return shutil.which(cmd, path=os.pathsep.join([os.environ.get("PATH", ""), *extra])) is not None


def _present(s, name, unsat):
    return unsat is None or name not in unsat or any(
        name in group and any(n not in unsat for n in group) for group in s.any_of
    ) or (name in s.cmds and _which(s.cmds[name]))


def _check(kind, value):
    if kind == "cmd":
        return _which(value)
    if kind == "file":
        return Path(value).expanduser().exists()
    try:
        return subprocess.run(["systemctl", "is-enabled", value],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def status(sets=None, unsat=_COMPUTED) -> dict[str, SetStatus]:
    sets = load() if sets is None else list(sets)
    if unsat is _COMPUTED:
        unsat = unsatisfied(_names(sets))
    result = {}
    for s in sets:
        names = _unique(s.repo + s.aur)
        missing = [n for n in names if not _present(s, n, unsat)]
        checks = []
        for kind, values in s.installed_if.items():
            values = [values] if isinstance(values, str) else values
            if not any(_check(kind, v) for v in values):
                checks.append(f"{kind}:{values[0]}")
        state = ("missing" if checks else "installed" if not missing else
                 "partial" if len(missing) < len(names) else "missing")
        result[s.id] = SetStatus(state, missing + checks)
    return result


def _select(ids, sets):
    ids = _unique(ids)
    unknown = [i for i in ids if i not in {s.id for s in sets}]
    if unknown:
        raise ValueError("unknown set(s): " + ", ".join(unknown))
    return [s for s in sets if s.id in ids]


def _run(argv, log):
    log(shlex.join(map(str, argv)))
    try:
        # No capture or pipes: sudo and package managers inherit the caller's TTY.
        return subprocess.run(list(map(str, argv))).returncode
    except OSError as e:
        log(str(e))
        return 1


def _result(selected, sets, removing=False, message=""):
    states = status(sets)
    failed = [s.id for s in selected if (states[s.id].state != "missing" if removing
                                       else states[s.id].state != "installed")]
    if failed:
        message = (message + "\n" if message else "") + (
            "If a download failed (404), the package database is stale: run cachy-update first, then retry.")
        if DB_LOCK.exists():
            message += "\nCheck that no pacman is running, then `sudo rm /var/lib/pacman/db.lck`."
    return InstallResult(int(bool(failed)), failed, message)


def install(ids, log=print) -> InstallResult:
    sets = load()
    selected = _select(ids, sets)
    unsat = unsatisfied(_names(sets))
    before = status(sets, unsat=unsat)
    repo = _unique(n for s in selected for n in s.repo if not _present(s, n, unsat))
    aur = _unique(n for s in selected for n in s.aur if not _present(s, n, unsat))
    failed_repo = []
    if repo and _run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *repo], log):
        failed_repo = [n for n in repo if _run(["sudo", "pacman", "-S", "--needed", "--noconfirm", n], log)]
    fallback = _unique(failed_repo + aur)
    message = ""
    if fallback:
        helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)
        if helper:
            _run([helper, "-S", "--needed", "--noconfirm", *fallback], log)
        else:
            message = "No AUR helper: install manually: " + " ".join(fallback)
            log(message)
    for s in selected:
        if s.step and (s.step_always or before[s.id].state != "installed"):
            _run(["bash", INSTALL_SH, "--only", s.step], log)
    return _result(selected, sets, message=message)


def remove(ids, log=print) -> InstallResult:
    sets = load()
    selected = _select(ids, sets)
    refused = [s.id for s in selected if not s.removable]
    if refused:
        return InstallResult(1, refused, "Cannot remove non-removable set(s): " + ", ".join(refused))
    before = status(sets)
    protected = {n for s in sets if s not in selected and before[s.id].state == "installed" for n in s.repo + s.aur}
    candidates = [n for n in _unique(n for s in selected for n in s.repo + s.aur) if n not in protected]
    if candidates:
        try:
            p = subprocess.run(["pacman", "-Qq", *candidates], capture_output=True, text=True)
        except FileNotFoundError:
            return InstallResult(1, [s.id for s in selected], "pacman not found")
        if p.returncode not in (0, 1):
            raise RuntimeError(f"pacman -Qq exited {p.returncode}: {p.stderr.strip()}")
        exact = set(p.stdout.splitlines())
        names = [n for n in candidates if n in exact]
        if names:
            _run(["sudo", "pacman", "-Rns", *names], log)
    return _result(selected, sets, removing=True)


def terminal_argv(action: str, ids: list[str], result: Path) -> list[str]:
    if action not in ("install", "remove"):
        raise ValueError("action must be install or remove")
    cmd = ["bash", "-c", 'cd "$1" && "$0" -m rice packages ' + action +
           ' --result "$2" "${@:3}"; read -rp "Press Enter to close" _',
           str(Path(sys.executable).absolute()), str(REPO), str(Path(result).absolute()), *ids]
    if shutil.which("xdg-terminal-exec"):
        return ["xdg-terminal-exec", *cmd]
    terminal = shlex.split(os.environ.get("TERMINAL", ""))
    if not terminal or Path(terminal[0]).name == "footclient" or not shutil.which(terminal[0]):
        terminal = next(([t] for t in ("wezterm", "kitty", "foot", "alacritty", "konsole") if shutil.which(t)), [])
    if not terminal:
        raise FileNotFoundError("No terminal found: set $TERMINAL or install xdg-terminal-exec")
    options = {
        "wezterm": ["start", "--always-new-process", "--cwd", str(REPO), "--"],
        "kitty": ["--"], "foot": ["--"], "alacritty": ["-e"],
        "konsole": ["--separate", "-e"], "gnome-terminal": ["--wait", "--"],
    }
    return [*terminal, *options.get(Path(terminal[0]).name, ["-e"]), *cmd]


def write_result(path, r) -> None:
    """Atomically write an InstallResult (done) or a running {state, pid} record.

    The CLI owns the running/finally lifecycle; callers choose F in XDG_RUNTIME_DIR
    (or tempfile), then pass it to terminal_argv and the CLI's --result option.
    """
    path = Path(path)
    data = {"state": "done", **asdict(r)} if isinstance(r, InstallResult) else dict(r)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as f:
            tmp = Path(f.name)
            json.dump(data, f)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _read_record(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_result(path) -> InstallResult | None:
    data = _read_record(path)
    if (data.get("state") == "done" and type(data.get("rc")) is int
            and isinstance(data.get("failed"), list) and all(isinstance(x, str) for x in data["failed"])
            and isinstance(data.get("message"), str)):
        return InstallResult(data["rc"], data["failed"], data["message"])
    return None


def poll_state(result: Path, launched_at: float, now: float) -> str:
    data = _read_record(result)
    if data.get("state") == "done" and read_result(result) is not None:
        return "done"
    if data.get("state") == "running":
        pid = data.get("pid")
        if type(pid) is not int or pid <= 0:
            return "cancelled"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "cancelled"
        except PermissionError:
            pass  # A live process owned by another uid is still alive.
        return "running"
    return "no-start" if now - launched_at > 30 else "running"
