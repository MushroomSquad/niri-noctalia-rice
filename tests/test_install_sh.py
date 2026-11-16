"""Installer plans run with real Python and guards against system operations."""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rice import packages

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / 'install.sh'


class InstallShTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.log = self.root / 'calls'
        # A closed PATH prevents accidental access to real system tools, even if
        # a regression gets past --print-plan. All mutating commands are absent.
        for name in ('bash', 'cat', 'dirname'):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / 'python').symlink_to(sys.executable)
        for name in ('sudo', 'pacman', 'wezterm', 'xdg-terminal-exec'):
            stub = self.bin / name
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "$INSTALL_TEST_LOG"\nexit 99\n')
            stub.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.bin), 'HOME': str(self.home),
                    'XDG_CONFIG_HOME': str(self.home / '.config'),
                    'XDG_DATA_HOME': str(self.home / '.local/share'),
                    'XDG_RUNTIME_DIR': str(self.root),
                    'NIRI_RICE_REEXECED': '1', 'INSTALL_TEST_LOG': str(self.log),
                    'TERMINAL': 'wezterm'}

    def run_installer(self, *args, rc=0):
        p = subprocess.run([str(self.bin / 'bash'), str(INSTALL), *args],
                           env=self.env, cwd=self.root, input='', text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else '')
        self.assertEqual(list(self.home.iterdir()), [], 'plan wrote user files')
        return p.stdout

    def plan(self, *args):
        output = self.run_installer('--yes', '--print-plan', *args)
        rows = dict(line.split(': ', 1) for line in output.splitlines())
        return rows['sets'].split(), shlex.split(rows['apply'])

    def test_no_greeter(self):
        sets, _ = self.plan('--no-greeter')
        self.assertNotIn('greeter', sets)

    def test_no_handy(self):
        sets, _ = self.plan('--no-handy')
        self.assertNotIn('handy', sets)

    def test_firedragon(self):
        sets, args = self.plan('--firedragon')
        self.assertIn('firedragon', sets)
        self.assertNotIn('firedragon', ' '.join(args))

    def test_no_firedragon(self):
        sets, args = self.plan('--no-firedragon')
        self.assertNotIn('firedragon', sets)
        self.assertNotIn('firedragon', ' '.join(args))

    def test_nvim(self):
        sets, args = self.plan('--nvim')
        self.assertIn('nvim', sets)
        self.assertIn('nvim', args[args.index('--with') + 1].split(','))

    def test_no_nvim(self):
        sets, args = self.plan('--no-nvim')
        self.assertNotIn('nvim', sets)
        self.assertIn('nvim', args[args.index('--without') + 1].split(','))

    def test_no_pkgs(self):
        sets, _ = self.plan('--no-pkgs')
        self.assertEqual(sets, [])

    def test_only_pkgs_removed(self):
        output = self.run_installer('--print-plan', '--only', 'pkgs', rc=1)
        self.assertIn('--only pkgs was removed: use python -m rice packages install <ids>', output)

    def test_only_unknown(self):
        self.assertIn('unknown --only step', self.run_installer('--only', 'bogus', rc=1))

    def test_only_validates_before_running(self):
        self.run_installer('--only', 'greeter,bogus', rc=1)

    def test_only_malformed(self):
        for steps in (',greeter', 'handy,', 'greeter,,handy'):
            with self.subTest(steps=steps):
                self.assertIn('invalid --only list', self.run_installer('--only', steps, rc=1))

    def test_generic_flags(self):
        sets, _ = self.plan('--sassc', '--no-micro', '--no-core')
        self.assertIn('sassc', sets)
        self.assertIn('core', sets)  # base sets are mandatory
        self.assertNotIn('micro', sets)

    def test_unknown_flag(self):
        self.assertIn('unknown package-set flag', self.run_installer('--print-plan', '--bogus', rc=1))

    def test_defaults_follow_manifest(self):
        sets, args = self.plan()
        self.assertEqual(sets, [s.id for s in packages.load() if s.group == 'base' or s.default])
        self.assertEqual(args[:3], ['apply', '--defaults', '--yes'])
        self.assertNotIn('--install-needs', args)

    def test_no_run_and_agents(self):
        self.env['RICE_NO_RUN'] = '1'
        _, args = self.plan('--no-agents')
        self.assertIn('--no-run', args)
        self.assertIn('agents', args[args.index('--without') + 1].split(','))

    def test_gui(self):
        self.assertEqual(self.plan('--gui')[1], ['gui'])

    def test_no_mokka(self):
        self.assertEqual(self.plan('--no-mokka')[1], ['skipped'])

    def test_only_dispatch_has_no_rice(self):
        text = INSTALL.read_text()
        block = text.split('step_handy() {', 1)[1].split('# END ONLY DISPATCH', 1)[0]
        self.assertNotIn('rice', block)
        self.assertNotIn('python -m', block)
        self.assertNotIn('git pull', block)
        dispatch = block.split('# BEGIN ONLY DISPATCH', 1)[1]
        names = re.search(r'^      ([a-z|]+)\) ;;', dispatch, re.M).group(1).split('|')
        self.assertEqual(set(names), {s.step for s in packages.load() if s.step})
        for name in names:
            self.assertIn(f'step_{name}() {{', text)

    def test_package_lists_are_manifest_only(self):
        text = INSTALL.read_text()
        self.assertNotIn('REPO_PKGS', text)
        self.assertNotIn('AUR_PKGS', text)
        lines = [line.strip() for line in text.splitlines() if 'pacman -S ' in line]
        self.assertEqual(lines, ['sudo pacman -S --needed --noconfirm python',
                                 'sudo pacman -S --needed --noconfirm cachy-update libnotify'])

    def test_bash_syntax(self):
        subprocess.run([shutil.which('bash'), '-n', str(INSTALL)], check=True)

    @unittest.skipUnless(shutil.which('shellcheck'), 'shellcheck is not installed')
    def test_shellcheck(self):
        subprocess.run([shutil.which('shellcheck'), str(INSTALL)], check=True)


if __name__ == '__main__':
    unittest.main()
