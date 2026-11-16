"""Fetch pinned upstream sources into ~/.cache/niri-noctalia-rice/upstream/<id>/<ref>/ (PLAN §5 T4.1)."""
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) / "niri-noctalia-rice"


def path(lid: str, entry: dict) -> Path | None:
    """Cached source (tree dir, or the single file for raw kinds); None if not fetched. No network."""
    d = cache_root() / "upstream" / lid / entry["ref"]
    if not d.is_dir():
        return None
    if entry["kind"] in ("raw-file", "pkgbuilds-path"):
        return next(d.iterdir())
    return d


def _download(url: str, dest: Path, sha: str) -> None:
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f)
    got = hashlib.sha256(part.read_bytes()).hexdigest()
    if got != sha:
        part.unlink()
        raise ValueError(f"sha256 mismatch for {url}: expected {sha}, got {got}")
    os.replace(part, dest)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def ensure(lid: str, entry: dict) -> Path:
    """Return the cached source, fetching it when missing (the only network entry point)."""
    have = path(lid, entry)
    if have:
        return have
    kind, ref, url = entry["kind"], entry["ref"], entry["url"]
    if kind == "git" and not shutil.which("git"):
        raise RuntimeError(f"git is required to fetch {lid} (pacman -S git)")
    final = cache_root() / "upstream" / lid / ref
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=final.parent))
    try:
        if kind == "git":
            _git(["init", "-q"], tmp)
            _git(["fetch", "-q", "--depth", "1", url, ref], tmp)
            _git(["checkout", "-q", "FETCH_HEAD"], tmp)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, check=True,
                                  capture_output=True, text=True).stdout.strip()
            if head != ref:
                raise ValueError(f"{lid}: fetched {head}, expected {ref}")
            shutil.rmtree(tmp / ".git")
        elif kind == "gitlab-archive":
            tgz = tmp / "src.tar.gz"
            _download(url, tgz, entry["sha256"])
            ex = tmp / "x"
            with tarfile.open(tgz) as t:
                t.extractall(ex, filter="data")
            tgz.unlink()
            tops = list(ex.iterdir())
            src = tops[0] if len(tops) == 1 and tops[0].is_dir() else ex
            for p in list(src.iterdir()):
                p.rename(tmp / p.name)
        else:  # raw-file / pkgbuilds-path
            _download(url, tmp / Path(url.split("?")[0]).name, entry["sha256"])
        # ponytail: tmp holds the tree directly (x/ left empty for archives)
        shutil.rmtree(tmp / "x", ignore_errors=True)
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return path(lid, entry)
