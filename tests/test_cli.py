import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from rice import cli, fetch, packages, state
from rice import lock as lock_mod
from tests.test_fetch import make_repo, make_tar


def toml_lock(d):
    return "".join(f"[{k}]\n" + "".join(f'{a} = "{b}"\n' for a, b in e.items()) + "\n" for k, e in d.items())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        t = self.tmp
        self.home = t / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_CACHE_HOME": str(t / "cache"),
                                           "XDG_STATE_HOME": str(t / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.refs = make_repo(t / "repo", ["v1\n", "v2\n"])
        tgz, sha = make_tar(t)
        self.lock_a = lambda i: {"src-a": {"url": (t / "repo").as_uri(), "kind": "git", "ref": self.refs[i]},
                                 "src-b": {"url": tgz.as_uri(), "kind": "gitlab-archive", "ref": "b1", "sha256": sha}}
        self.lockfile = t / "lock.toml"
        self.lockfile.write_text(toml_lock(self.lock_a(0)))
        self.comps = t / "components"
        self.comps.mkdir()
        self.counter = t / "counter"
        for p in (mock.patch.object(cli, "COMPONENTS_DIR", self.comps), mock.patch.object(lock_mod, "LOCK_PATH", self.lockfile)):
            p.start()
            self.addCleanup(p.stop)
        self.write("alpha", f'''id="alpha"
title="A"
group="look"
default=true
sources=["src-a"]
[[steps]]
op="copy"
src="src-a:a.txt"
dst=".config/alpha/a.txt"
[[steps]]
op="toml_set"
target=".config/shared.toml"
key="x.alpha"
value=1
[[steps]]
op="run"
argv=["sh","-c","echo x >> {self.counter}"]
''')
        self.write("beta", '''id="beta"
title="B"
group="look"
default=true
sources=["src-b"]
niri_snippet="cursor { xcursor-size 24; }"
[[steps]]
op="copy"
src="src-b:b.txt"
dst=".config/beta/b.txt"
[[steps]]
op="toml_set"
target=".config/shared.toml"
key="x.beta"
value=2
[[steps]]
op="ini_set"
target=".config/beta.ini"
section="S"
key="k"
value="on"
[[off_steps]]
op="ini_set"
target=".config/beta.ini"
section="S"
key="k"
value="off"
''')

    def write(self, name, text):
        (self.comps / f"{name}.toml").write_text(text)

    def runs(self):
        return len(self.counter.read_text().split()) if self.counter.exists() else 0

    def baks(self):
        return [p for p in self.home.rglob("*.bak-*")]

    def ids(self, *extra):
        return ["alpha", "beta", *extra]

    def test_idempotent_apply_and_run_steps(self):
        r = cli.apply(self.ids())
        self.assertEqual({k: v.status for k, v in r.items()}, {"alpha": "applied", "beta": "applied"})
        self.assertEqual(self.runs(), 1)
        baks = self.baks()  # beta edits the file alpha just created: one legit backup
        r = cli.apply(self.ids())
        self.assertEqual({v.status for v in r.values()}, {"unchanged"})
        self.assertEqual(self.baks(), baks)
        self.assertEqual(self.runs(), 1)
        st = cli.status()
        self.assertFalse(any(s.needs_apply or s.stale_sources or s.modified or s.recipe_changed for s in st.values() if s.enabled))
        self.write("alpha", (self.comps / "alpha.toml").read_text().replace("echo x", "echo y"))
        cli.apply(self.ids())
        self.assertEqual(self.runs(), 2)

    def test_apply_run_repo_path_from_another_home(self):
        repo = self.tmp / "repository with spaces"
        repo.mkdir()
        (repo / "record.sh").write_text('printf "%s" "$PWD" > "$1"\n')
        steps = [{"argv": ["sh", "{repo}/record.sh", str(self.counter)]}]
        with mock.patch.object(cli, "REPO", repo):
            self.assertTrue(cli._run_steps(steps, self.home, lambda _: None))
        self.assertEqual(self.counter.read_text(), str(self.home))

    def test_apply_fetches_and_dry_run_does_not(self):
        r = cli.apply(self.ids(), dry_run=True)
        self.assertFalse((fetch.cache_root() / "upstream").exists())
        self.assertEqual(r["alpha"].status, "skipped")
        cli.apply(["alpha"])
        self.assertTrue((self.home / ".config/alpha/a.txt").exists())
        self.assertTrue((fetch.cache_root() / "upstream/src-a").is_dir())
        # enabled but sources gone from cache -> not built yet
        import shutil
        shutil.rmtree(fetch.cache_root())
        self.assertIsNone(cli.status()["alpha"].needs_apply)

    def test_custom_seed_once_per_apply_and_never_owned(self):
        custom = self.home / ".config/rice-custom"
        with mock.patch.object(cli, "_seed_custom", wraps=cli._seed_custom) as seed:
            cli.apply(self.ids(), run=False)
            seed.assert_called_once_with(self.home)
        skel = cli.REPO / "mokka/custom-skel"
        for src in skel.rglob("*"):
            if src.is_file():
                self.assertEqual((custom / src.relative_to(skel)).read_bytes(), src.read_bytes())
        self.assertFalse((custom / "starship.toml").exists())
        (custom / "niri.kdl").write_text("// user edit\n")
        user = custom / "fish/x.fish"
        user.write_text("alias ls 'echo custom'\n")
        (custom / "gtk3.css").unlink()
        cli.apply(self.ids(), force=True, run=False)
        self.assertEqual((custom / "niri.kdl").read_text(), "// user edit\n")
        self.assertEqual(user.read_text(), "alias ls 'echo custom'\n")
        self.assertEqual((custom / "gtk3.css").read_bytes(), (skel / "gtk3.css").read_bytes())
        self.assertFalse(any("rice-custom" in p for p in cli.owned()))
        self.assertNotIn("rice-custom", json.dumps(state.load()))
        self.assertFalse(list(custom.rglob("*.bak-*")))

    def test_custom_seed_absent_on_dry_run_and_status(self):
        with mock.patch.object(cli, "_seed_custom", wraps=cli._seed_custom) as seed:
            cli.apply(self.ids(), dry_run=True, run=False)
            cli.status()
            seed.assert_not_called()
        self.assertFalse((self.home / ".config/rice-custom").exists())
        # A dry run must not re-create a deleted seed in an already applied HOME either.
        cli.apply(self.ids(), run=False)
        example = self.home / ".config/rice-custom/gtk4.css"
        example.unlink()
        cli.apply(self.ids(), dry_run=True, run=False)
        self.assertFalse(example.exists())

    def test_dry_run_prints_zero_changes(self):
        cli.apply(self.ids())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["apply", "alpha", "beta", "--dry-run", "--no-run"])
        self.assertEqual(code, 0)
        self.assertIn("0 changes", out.getvalue().splitlines())

    def test_defaults_sticky(self):
        cli.apply(self.ids())
        cli.disable(["alpha"])
        ns = mock.Mock(ids=[], defaults=True, with_=None, without=None)
        sel = cli._select(ns, cli.components())
        self.assertEqual(sel, ["beta"])
        fresh = state.load()
        self.assertTrue(fresh["alpha"]["disabled"])

    def test_no_run_then_retry(self):
        cli.apply(self.ids(), run=False)
        self.assertEqual(self.runs(), 0)
        self.assertTrue(state.load()["alpha"]["run_pending"])
        self.assertTrue(cli.status()["alpha"].needs_apply)
        d = cli.apply(self.ids(), dry_run=True, run=False)
        self.assertFalse(any(c.kind == "run" for r in d.values() for c in r.changes))
        cli.apply(self.ids())
        self.assertEqual(self.runs(), 1)
        self.assertFalse(state.load()["alpha"]["run_pending"])

    def test_failing_run_keeps_pending(self):
        self.write("fail", '''id="fail"
title="F"
group="look"
[[steps]]
op="run"
argv=["false"]
''')
        r = cli.apply(["fail"])
        self.assertEqual(r["fail"].status, "failed")
        self.assertTrue(state.load()["fail"]["run_pending"])
        self.assertTrue(cli.status()["fail"].needs_apply)

    def test_dry_run_never_builds(self):
        marker = self.tmp / "built"
        self.write("bld", f'''id="bld"
title="B"
group="look"
[[steps]]
op="run"
when="build"
argv=["sh","-c","touch {marker}; touch {{out}}/x"]
''')
        cli.apply(["bld"], dry_run=True)
        self.assertFalse(marker.exists())
        cli.apply(["bld"])
        self.assertTrue(marker.exists())

    def test_shared_file_and_noctalia_rewrite(self):
        cli.apply(self.ids())
        f = self.home / ".config/shared.toml"
        d = tomllib.loads(f.read_text())
        self.assertEqual(d["x"], {"alpha": 1, "beta": 2})
        f.write_text(f.read_text() + '[theme]\ncolor = "#fff"\n')
        self.assertEqual({v.status for v in cli.apply(self.ids(), dry_run=True).values()}, {"unchanged"})
        self.assertFalse(any(s.needs_apply for s in cli.status().values() if s.enabled))

    def test_selective_update(self):
        cli.apply(self.ids())
        new = toml_lock(self.lock_a(1))

        def fake(pk, write=True):
            if write:
                self.lockfile.write_text(new)
            return {"src-a": (self.refs[0], self.refs[1])}

        self.lockfile.write_text(new)
        st = cli.status()
        self.assertEqual(st["alpha"].stale_sources, ["src-a"])
        self.assertEqual(st["beta"].stale_sources, [])
        self.lockfile.write_text(toml_lock(self.lock_a(0)))
        with mock.patch.object(lock_mod, "lock", fake):
            moved, res = cli.update(yes=True, log=lambda *_: None)
        self.assertEqual(list(res), ["alpha"])
        self.assertEqual(res["alpha"].status, "applied")
        self.assertEqual((self.home / ".config/alpha/a.txt").read_text(), "v2\n")

    def test_disable_restores_and_drops_snippet(self):
        cli.apply(self.ids())
        kdl = self.home / state.RICE_KDL
        self.assertIn("xcursor-size", kdl.read_text())
        cli.disable(["beta"])
        self.assertNotIn("xcursor-size", kdl.read_text())
        self.assertIn("k=off", (self.home / ".config/beta.ini").read_text())
        self.assertIn(".config/beta/b.txt", cli.owned())
        self.assertFalse(cli.status()["beta"].enabled)

    def test_hand_edit_not_overwritten(self):
        cli.apply(self.ids())
        p = self.home / ".config/alpha/a.txt"
        p.write_text("mine\n")
        self.assertEqual(cli.status()["alpha"].modified, [".config/alpha/a.txt"])
        self.assertEqual(cli.apply(["alpha"])["alpha"].status, "modified")
        self.assertEqual(p.read_text(), "mine\n")
        self.assertEqual(cli.apply(["alpha"], force=True)["alpha"].status, "applied")
        self.assertEqual(p.read_text(), "v1\n")

    def test_help_lists_commands(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        for c in "lock fetch apply disable status update owned refresh gui".split():
            self.assertIn(c, out.getvalue())


MANIFEST = """[[set]]
id = "x"
title = "X"
group = "apps"
repo = ["xpkg"]
"""


class NeedsTest(unittest.TestCase):
    setUp = CliTest.setUp
    write = CliTest.write
    ids = CliTest.ids

    def start(self):
        (self.tmp / "packages.toml").write_text(MANIFEST)
        self.unsat = {"xpkg"}
        for p in (mock.patch.object(packages, "MANIFEST", self.tmp / "packages.toml"),
                  mock.patch.object(packages, "unsatisfied", lambda names: set(self.unsat))):
            p.start()
            self.addCleanup(p.stop)
        self.write("gamma", '''id="gamma"
title="G"
group="look"
default=true
needs=["x"]
[[steps]]
op="toml_set"
target=".config/g.toml"
key="g"
value=1
''')

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_unmet_need_skips_without_state_or_files(self):
        self.start()
        r = cli.apply(["gamma"], run=False)["gamma"]
        self.assertEqual((r.status, r.message), ("skipped", "needs x"))
        self.assertNotIn("gamma", state.load())
        self.assertFalse((self.home / ".config/g.toml").exists())

    def test_exit_codes(self):
        self.start()
        self.assertEqual(self.run_main("apply", "gamma", "--dry-run", "--no-run")[0], 1)
        self.assertEqual(self.run_main("apply", "--defaults", "--dry-run", "--no-run")[0], 0)
        self.assertEqual(self.run_main("apply", "--defaults", "--dry-run", "--no-run", "--no-packages")[0], 0)

    def test_install_needs_installs_once_only_missing(self):
        self.start()

        def fake(ids, log=print):
            self.unsat = set()
            return packages.InstallResult(0, [], "")
        with mock.patch.object(packages, "install", side_effect=fake) as inst:
            r = cli.apply(["gamma", "alpha"], install_needs=True, run=False)
        inst.assert_called_once()
        self.assertEqual(inst.call_args[0][0], ["x"])
        self.assertEqual(r["gamma"].status, "applied")

    def test_dry_run_never_installs(self):
        self.start()
        with mock.patch.object(packages, "install") as inst:
            r = cli.apply(["gamma"], dry_run=True, install_needs=True, run=False)
        inst.assert_not_called()
        self.assertEqual(r["gamma"].status, "skipped")

    def test_status_fields_and_flags(self):
        self.start()
        cli.apply(["alpha"])
        st = state.load()
        st["alpha"]["run_pending"] = True
        state.save(st)
        s = cli.status()
        self.assertEqual(s["gamma"].missing_needs, ["x"])
        self.assertTrue(s["alpha"].run_pending)
        self.assertFalse(s["alpha"].missing_needs)
        out = self.run_main("status")[1]
        self.assertIn("needs:x", out)
        self.assertIn("run-pending", out)
        self.unsat = set()
        self.assertEqual(cli.status()["gamma"].missing_needs, [])

    def test_packages_list_and_status(self):
        self.start()
        self.assertEqual(self.run_main("packages", "list")[1], "x\tapps\t0\t-\tX\n")
        self.assertEqual(self.run_main("packages", "status")[1], "x: not installed\n")
        self.unsat = set()
        self.assertEqual(self.run_main("packages", "status")[1], "x: installed\n")

    def test_result_file_lifecycle(self):
        self.start()
        f = self.tmp / "F.json"
        seen = []

        def ok(ids, log=print):
            seen.append(json.loads(f.read_text()))
            return packages.InstallResult(0, [], "fine")
        with mock.patch.object(packages, "install", side_effect=ok):
            code, out = self.run_main("packages", "install", "x", "--result", str(f))
        self.assertEqual((code, seen[0]["state"], seen[0]["pid"]), (0, "running", os.getpid()))
        self.assertEqual(packages.read_result(f), packages.InstallResult(0, [], "fine"))
        self.assertIn("fine", out)
        with mock.patch.object(packages, "install", side_effect=RuntimeError("boom")):
            code, _ = self.run_main("packages", "install", "x", "--result", str(f))
        r = packages.read_result(f)
        self.assertEqual((code, r.rc, r.failed), (1, 1, ["x"]))
        self.assertIn("boom", r.message)
        with mock.patch.object(packages, "install", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_main("packages", "install", "x", "--result", str(f))
        self.assertEqual(json.loads(f.read_text())["state"], "done")

    def test_old_state_compat(self):
        self.start()
        old = {"yakuake": {"whole": {".local/share/yakuake/skins/mocha/x": "abc"}, "variant": None},
               "firedragon": {"run_pending": True}}
        state.save(old)
        raw = state.state_path().read_text()
        self.assertNotIn("yakuake", cli.status())
        cli.apply(["alpha"], dry_run=True, run=False)
        self.assertEqual(self.run_main("apply", "--defaults", "--dry-run", "--no-run")[0], 0)
        r = cli.disable(["yakuake"])["yakuake"]
        self.assertEqual((r.status, r.message), ("failed", "unknown component yakuake"))
        self.assertIn(".local/share/yakuake/skins/mocha/x", cli.owned())
        self.assertEqual(state.state_path().read_text(), raw)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["packages", "install", "yakuake"]), 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["apply", "--help"])
        self.assertNotIn("--no-packages", out.getvalue())


if __name__ == "__main__":
    unittest.main()
