"""Parse upstream PKGBUILDs and write mokka/upstream.lock.toml (stdlib only, no bash execution)."""
import json
import re
import shlex
import tomllib
import urllib.request
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent / "mokka" / "upstream.lock.toml"
RAW = "https://gitlab.com/garuda-linux/pkgbuilds/-/raw/{}/{}/{}"
API = "https://gitlab.com/api/v4/projects/garuda-linux%2Fpkgbuilds/repository/commits/main"

# lock id -> (package, substring selecting one source item)
IDS = {
    "garuda-mokka": ("garuda-mokka", "/-/archive/"),
    "catppuccin-kde": ("garuda-mokka", "catppuccin/kde"),
    "catppuccin-micro": ("garuda-mokka", "catppuccin/micro"),
    "catppuccin-conky": ("garuda-mokka", "catppuccin/conky"),
    "catppuccin-konsole": ("garuda-mokka", "catppuccin/konsole"),
    "catppuccin-bat": ("garuda-mokka", "catppuccin/bat"),
    "catppuccin-grub": ("garuda-mokka", "catppuccin/grub"),
    "colloid": ("garuda-mokka", "Colloid-gtk-theme"),
    "garuda-wallpapers": ("garuda-wallpapers", "/-/archive/"),
    "done": ("garuda-fish-config", "done.fish"),
    "garuda-fish-config": ("garuda-fish-config", "config.fish"),
    "garuda-starship-prompt": ("garuda-starship-prompt", "starship.toml"),
}


def parse_pkgbuild(text):
    """Column-0 assignments up to the first function. Scalars -> str, arrays -> [(item, lineno)]."""
    out, arr = {}, None
    for n, line in enumerate(text.splitlines(), 1):
        if arr is None:
            if re.match(r"\w+\(\)\s*\{", line):
                break
            m = re.match(r"(\w+)=(.*)", line)
            if not m:
                continue
            name, val = m.groups()
            if not val.startswith("("):
                toks = shlex.split(val)
                out[name] = _expand(toks[0], out) if toks else ""
                continue
            arr, line = (name, []), val[1:]
        stripped = line.rstrip()
        if stripped.lstrip().startswith("#"):
            continue
        done = stripped.endswith(")")
        if done:
            stripped = stripped[:-1]
        arr[1].extend((_expand(t, out), n) for t in shlex.split(stripped))
        if done:
            out[arr[0]], arr = arr[1], None
    src, sums = out.get("source", []), out.get("sha256sums", [])
    assert len(src) == len(sums), f"source/sha256sums length mismatch: {len(src)} != {len(sums)}"
    return out


def _expand(s, scalars):
    return re.sub(r"\$\{(\w+)\}|\$(\w+)",
                  lambda m: scalars.get(m[1] or m[2], m[0]) if isinstance(scalars.get(m[1] or m[2]), str) else m[0], s)


def classify(pkg, item, sha, commit):
    """-> {url, kind, ref, sha256?} for one source item."""
    if "/-/archive/" in item:
        return {"url": item, "kind": "gitlab-archive", "ref": re.search(r"/-/archive/([0-9a-f]{40})/", item)[1], "sha256": sha}
    if item.startswith("git+"):
        url, ref = item[4:].split("#commit=")
        return {"url": url.removesuffix(".git") + ".git", "kind": "git", "ref": ref}
    if "://" in item:
        return {"url": item, "kind": "raw-file", "ref": re.search(r"raw\.githubusercontent\.com/[^/]+/[^/]+/([^/]+)/", item)[1], "sha256": sha}
    return {"url": RAW.format(commit, pkg, item), "kind": "pkgbuilds-path", "ref": commit, "sha256": sha}


def make_lock(commit, read_pkgbuild):
    """read_pkgbuild(pkg) -> PKGBUILD text. Returns {lock id: fields}."""
    parsed, lock = {}, {}
    for lid, (pkg, sel) in IDS.items():
        p = parsed.get(pkg) or parsed.setdefault(pkg, parse_pkgbuild(read_pkgbuild(pkg)))
        hits = [i for i, (s, _) in enumerate(p["source"]) if sel in s]
        assert len(hits) == 1, f"{lid}: {len(hits)} sources match {sel!r} in {pkg}"
        item, line = p["source"][hits[0]]
        entry = classify(pkg, item, p["sha256sums"][hits[0]][0], commit)
        entry["derived_from"] = f"{pkg}/PKGBUILD:{line}"
        lock[lid] = entry
    return lock


def dumps(lock):
    return "".join(f"[{lid}]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in e.items()) + "\n"
                   for lid, e in lock.items())


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode()


def lock(pkgbuilds, write=True):
    """pkgbuilds: a pkgbuilds commit or "latest". Returns {id: (old_ref|None, new_ref)} for moved pins."""
    commit = json.loads(_get(API))["id"] if pkgbuilds == "latest" else pkgbuilds
    new = make_lock(commit, lambda pkg: _get(RAW.format(commit, pkg, "PKGBUILD")))
    old = tomllib.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else {}
    moved = {i: (old.get(i, {}).get("ref"), e["ref"]) for i, e in new.items() if old.get(i, {}).get("ref") != e["ref"]}
    if write:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.write_text(dumps(new))
    return moved
