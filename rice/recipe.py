"""Recipe loader and the 8 ops (copy, generate, run, replace, drop_lines, ini_set, toml_set, json_set).

Step schema (TOML `[[steps]]`, also `[[off_steps]]` and `[[variants.<n>.steps]]`), key `op`:
  copy        src="<lock-id>:<path>"|"repo:<path>", dst=<relpath under HOME>, track=true
  generate    generator=<name>                       (injected callable, see Generator)
  run         argv=[...], when="apply"|"build", src=<lock-id>?, terminal=false
              (build: cwd is a scratch copy of source `src`; "{out}" in argv is the out dir)
  replace     target, pattern (regex), repl           ($HOME in repl is substituted)
  drop_lines  target, pattern (regex)
  ini_set     target, section, key, value
  toml_set    target, key (dotted), value             (value: literal | {add=x} | {remove=x})
  json_set    target, key (dotted), value
Patch `target` is a HOME relpath, or "<lock-id>:<path>" for a file in the source scratch copy.
"""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

Generator = Callable[[dict[str, Path], Path], dict[str, Path]]

GROUPS = {"look", "qt", "gtk", "shell", "terminal", "apps", "wallpapers"}
PATCH_OPS = {"replace", "drop_lines", "ini_set", "toml_set", "json_set"}
OPS = PATCH_OPS | {"copy", "generate", "run"}
REQUIRED = {
    "copy": ("src", "dst"), "generate": ("generator",), "run": ("argv",),
    "replace": ("target", "pattern", "repl"), "drop_lines": ("target", "pattern"),
    "ini_set": ("target", "section", "key", "value"),
    "toml_set": ("target", "key", "value"), "json_set": ("target", "key", "value"),
}
SRC_RE = re.compile(r"^[\w.-]+:.+$")


@dataclass
class Variant:
    name: str
    title: str
    packages: dict
    steps: list


@dataclass
class Component:
    id: str
    title: str
    description: str
    group: str
    default: bool
    conflicts: list
    requires: list
    packages: dict
    only_if_installed: str | None
    previews: list
    sources: list
    steps: list
    off_steps: list | None
    niri_snippet: str | None
    variants: dict
    default_variant: str | None
    path: Path
    reversible: bool = field(default=True)


def _check_steps(steps, where):
    if not isinstance(steps, list):
        raise ValueError(f"{where}: must be an array of tables")
    for i, s in enumerate(steps):
        w = f"{where}[{i}]"
        if s.get("op") not in OPS:
            raise ValueError(f"{w}.op: must be one of {sorted(OPS)}, got {s.get('op')!r}")
        for k in REQUIRED[s["op"]]:
            if k not in s:
                raise ValueError(f"{w}.{k}: missing for op {s['op']}")
        if s["op"] == "copy" and not SRC_RE.match(s["src"]):
            raise ValueError(f"{w}.src: expected '<lock-id>:<path>' or 'repo:<path>', got {s['src']!r}")
        if s["op"] == "run":
            if s.get("when", "apply") not in ("apply", "build"):
                raise ValueError(f"{w}.when: must be apply or build")
            if not isinstance(s["argv"], list) or not s["argv"]:
                raise ValueError(f"{w}.argv: must be a non-empty list")
        if s["op"] in ("replace", "drop_lines"):
            re.compile(s["pattern"])
        if s["op"] == "toml_set" and isinstance(s["value"], dict) and len(s["value"]) != 1:
            raise ValueError(f"{w}.value: table must be exactly {{add=..}} or {{remove=..}}")
        if s["op"] == "toml_set" and isinstance(s["value"], dict) and next(iter(s["value"])) not in ("add", "remove"):
            raise ValueError(f"{w}.value: table must be exactly {{add=..}} or {{remove=..}}")


def load(path) -> Component:
    path = Path(path)
    try:
        d = tomllib.loads(path.read_text())
        for k in ("id", "title", "group"):
            if k not in d:
                raise ValueError(f"{k}: missing")
        if d["group"] not in GROUPS:
            raise ValueError(f"group: must be one of {sorted(GROUPS)}, got {d['group']!r}")
        _check_steps(d.get("steps", []), "steps")
        if "off_steps" in d:
            _check_steps(d["off_steps"], "off_steps")
        variants = {}
        for n, v in d.get("variants", {}).items():
            _check_steps(v.get("steps", []), f"variants.{n}.steps")
            variants[n] = Variant(n, v.get("title", n), v.get("packages", {}), v.get("steps", []))
        dv = d.get("default_variant")
        if variants and dv not in variants:
            raise ValueError(f"default_variant: {dv!r} is not one of {sorted(variants)}")
        if dv and not variants:
            raise ValueError("default_variant: set but no variants defined")
    except (ValueError, tomllib.TOMLDecodeError, re.error) as e:
        raise ValueError(f"{path}: {e}") from e
    every = d.get("steps", []) + [s for v in variants.values() for s in v.steps]
    reversible = "off_steps" in d or not any(s["op"] in PATCH_OPS for s in every)
    return Component(
        id=d["id"], title=d["title"], description=d.get("description", ""), group=d["group"],
        default=d.get("default", False), conflicts=d.get("conflicts", []), requires=d.get("requires", []),
        packages=d.get("packages", {}), only_if_installed=d.get("only_if_installed"),
        previews=d.get("previews", []), sources=d.get("sources", []), steps=d.get("steps", []),
        off_steps=d.get("off_steps"), niri_snippet=d.get("niri_snippet"), variants=variants,
        default_variant=dv, path=path, reversible=reversible)


def all_steps(c: Component, variant: str | None = None) -> list:
    """Component steps followed by the chosen variant's steps (default variant if None)."""
    variant = variant or c.default_variant
    return c.steps + (c.variants[variant].steps if variant else [])


def recipe_hash(c: Component, variant: str | None = None) -> str:
    variant = variant or c.default_variant
    blob = {"sources": c.sources, "steps": all_steps(c, variant), "off_steps": c.off_steps,
            "niri_snippet": c.niri_snippet, "variant": variant}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def patch_ops(c: Component, variant: str | None = None) -> dict[str, list]:
    """Patch ops grouped by target, in step order."""
    out: dict[str, list] = {}
    for s in all_steps(c, variant):
        if s["op"] in PATCH_OPS:
            out.setdefault(s["target"], []).append(s)
    return out


# ---------------------------------------------------------------- patches (text -> text)

def _lines(text):
    return text.splitlines(keepends=True)


def _insert(lines, pos, new):
    if pos and not lines[pos - 1].endswith("\n"):
        lines[pos - 1] += "\n"
    lines.insert(pos, new)


def _region(lines, header):
    """(start, end) of the body after the line stripping to `header` (None = top level)."""
    start = 0
    if header is not None:
        idx = next((i for i, l in enumerate(lines) if l.strip() == header), None)
        if idx is None:
            return None
        start = idx + 1
    end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return start, end


def _after_last(lines, start, end):
    p = end
    while p > start and not lines[p - 1].strip():
        p -= 1
    return p


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return repr(v)


def _value_end(lines, i, col):
    """Last line index of the TOML value starting at lines[i][col:] (bracket matching for arrays)."""
    depth, quote = 0, None
    for j in range(i, len(lines)):
        line = lines[j]
        for ch in line[col if j == i else 0:]:
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                break
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
        quote = None
        if depth <= 0:
            return j
    return len(lines) - 1


def _ini_set(text, op):
    section, key, value = op["section"], op["key"], str(op["value"])
    lines = _lines(text)
    reg = _region(lines, f"[{section}]")
    if reg is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines:
            lines.append("\n")
        lines += [f"[{section}]\n", f"{key}={value}\n"]
        return "".join(lines)
    start, end = reg
    rx = re.compile(r"^(" + re.escape(key) + r"(?:\[\$e\])?\s*=\s*)(.*?)(\r?\n?)$")
    for i in range(start, end):
        m = rx.match(lines[i])
        if m:
            if m.group(2).strip() != value:
                lines[i] = m.group(1) + value + m.group(3)
            return "".join(lines)
    _insert(lines, _after_last(lines, start, end), f"{key}={value}\n")
    return "".join(lines)


def _toml_set(text, op):
    table, _, name = op["key"].rpartition(".")
    value = op["value"]
    lines = _lines(text)
    reg = _region(lines, f"[{table}]" if table else None)
    if reg is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines:
            lines.append("\n")
        new = _new_toml_value(None, value)
        return "".join(lines + [f"[{table}]\n", f"{name} = {_fmt(new)}\n"])
    start, end = reg
    rx = re.compile(r"^(\s*)" + re.escape(name) + r"(\s*=\s*)(.*)$")
    for i in range(start, end):
        m = rx.match(lines[i])
        if not m:
            continue
        j = _value_end(lines, i, m.start(3))
        raw = m.group(3) + "".join(lines[i + 1:j + 1])
        cur = tomllib.loads("v = " + raw)["v"]
        new = _new_toml_value(cur, value)
        if new == cur:
            return text
        if j > i and isinstance(new, list):
            ind = m.group(1)
            body = ",\n".join(f'{ind}    {_fmt(x)}' for x in new)
            rep = f"{ind}{name}{m.group(2)}[\n{body}\n{ind}]\n"
        else:
            rep = f"{m.group(1)}{name}{m.group(2)}{_fmt(new)}\n"
        lines[i:j + 1] = [rep]
        return "".join(lines)
    p = _after_last(lines, start, end)
    ind = re.match(r"\s*", lines[p - 1]).group() if p > start else ("    " if table else "")
    _insert(lines, p, f"{ind}{name} = {_fmt(_new_toml_value(None, value))}\n")
    return "".join(lines)


def _new_toml_value(cur, value):
    if not isinstance(value, dict):
        return value
    (kind, item), = value.items()
    cur = list(cur or [])
    if kind == "add":
        return cur if item in cur else cur + [item]
    return [x for x in cur if x != item]


def _json_set(text, op):
    data = json.loads(text) if text.strip() else {}
    *path, last = op["key"].split(".")
    node = data
    for p in path:
        node = node.setdefault(p, {})
    if node.get(last, object()) == op["value"]:
        return text
    node[last] = op["value"]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def patch(text: str, op: dict, home) -> str:
    kind = op["op"]
    if kind == "replace":
        repl = op["repl"].replace("$HOME", str(home))
        return re.sub(op["pattern"], lambda m: repl, text, flags=re.M)
    if kind == "drop_lines":
        rx = re.compile(op["pattern"])
        return "".join(l for l in _lines(text) if not rx.search(l))
    return {"ini_set": _ini_set, "toml_set": _toml_set, "json_set": _json_set}[kind](text, op)


# ---------------------------------------------------------------- whole outputs

def _resolve(ref, sources, repo_root):
    sid, _, rel = ref.partition(":")
    base = Path(repo_root) if sid == "repo" else Path(sources[sid])
    return base if base.is_file() else base / rel


def _files(root):
    return {p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file()}


def render_whole(c: Component, variant, sources, repo_root, out_dir, generators) -> dict[str, tuple[Path, bool]]:
    """Render copy/generate/run(build) outputs into out_dir -> {relpath: (path, track)}.

    Patches targeting a rendered file are applied to it here (before it is hashed); patches on
    "<lock-id>:<path>" apply to a scratch copy of the source before `run when=build`. Remaining
    patch targets are live patches for the caller (see patch_ops).
    """
    variant = variant or c.default_variant
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = all_steps(c, variant)
    home = Path.home()  # $HOME in staged patches; honours the HOME env
    whole: dict[str, tuple[Path, bool]] = {}

    def stage(rel, src, track):
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        whole[rel] = (dst, track)

    for s in steps:
        if s["op"] == "copy":
            src = _resolve(s["src"], sources, repo_root)
            track = s.get("track", True)
            dst = s["dst"]
            if src.is_dir():
                for f in sorted(src.rglob("*")):
                    if f.is_file():
                        stage((Path(dst) / f.relative_to(src)).as_posix(), f, track)
            else:
                stage((Path(dst) / src.name).as_posix() if dst.endswith("/") else dst, src, track)
        elif s["op"] == "generate":
            for rel, p in generators[s["generator"]](sources, out_dir).items():
                whole[rel] = (Path(p), True)

    scratch_root = Path(tempfile.mkdtemp(prefix="rice-build-"))
    try:
        scratch: dict[str, Path] = {}

        def scratch_of(sid):
            if sid not in scratch:
                scratch[sid] = scratch_root / sid
                src = Path(sources[sid])
                (shutil.copytree(src, scratch[sid], symlinks=True) if src.is_dir()
                 else (scratch[sid].mkdir(), shutil.copy2(src, scratch[sid] / src.name)))
            return scratch[sid]

        for s in steps:
            if s["op"] not in PATCH_OPS:
                continue
            t = s["target"]
            sid, _, rel = t.partition(":")
            if ":" in t and sid in sources:
                f = scratch_of(sid) / rel
            elif t in whole:
                f = whole[t][0]
            else:
                continue
            f.write_text(patch(f.read_text(), s, home))
        for s in steps:
            if s["op"] == "run" and s.get("when", "apply") == "build":
                before = _files(out_dir)
                cwd = scratch_of(s["src"]) if "src" in s else out_dir
                subprocess.run([a.replace("{out}", str(out_dir)) for a in s["argv"]], cwd=cwd, check=True)
                for rel in sorted(_files(out_dir) - before):
                    whole[rel] = (out_dir / rel, True)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    return whole
