"""Package operations run exclusively against executables in a temporary PATH."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from rice import packages as p

FAKE = r'''
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['PKG_LOG'], 'a') as f:
    f.write(json.dumps([name, *args]) + '\n')
db = Path(os.environ['PKG_DB'])
installed = set(json.loads(db.read_text()))
provided = set(os.environ.get('PKG_PROVIDES', '').split())
if name == 'sudo':
    sys.exit(subprocess.run(args).returncode)
if name == 'systemctl':
    sys.exit(int(os.environ.get('PKG_UNIT_RC', '0')))
if name in ('pacman', 'paru', 'yay'):
    op = args[0]
    names = [a for a in args[1:] if not a.startswith('-')]
    if op == '-T':
        if os.environ.get('PKG_TEST_RC'):
            sys.exit(int(os.environ['PKG_TEST_RC']))
        missing = [n for n in names if n not in installed | provided]
        if missing:
            print('\n'.join(missing))
        sys.exit(127 if missing else 0)
    if op == '-Qq':
        exact = [n for n in names if n in installed]
        if exact:
            print('\n'.join(exact))
        sys.exit(int(len(exact) != len(names)))
    if op == '-S':
        if name == 'pacman' and (os.environ.get('PKG_BATCH_FAIL') and len(names) > 1
                                or any(n in os.environ.get('PKG_REPO_FAIL', '').split() for n in names)):
            sys.exit(1)
        if os.environ.get('PKG_NO_CHANGE'):
            sys.exit(0)
        installed.update(names)
    elif op == '-Rns':
        installed.difference_update(names)
    else:
        sys.exit('unexpected package operation')
    db.write_text(json.dumps(sorted(installed)))
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.db = self.root / 'db.json'
        self.db.write_text('[]')
        self.log = self.root / 'calls.jsonl'
        self.manifest = self.root / 'packages.toml'
        self.lock = self.root / 'db.lck'
        self.script = self.root / 'install.sh'
        self.script.write_text('printf "step:%s\\n" "$*" >> "$STEP_LOG"\n')
        env = dict(PATH=str(self.bin), HOME=str(self.root), XDG_RUNTIME_DIR=str(self.root),
                   TERMINAL='', PKG_DB=str(self.db), PKG_LOG=str(self.log),
                   STEP_LOG=str(self.root / 'steps'), PKG_UNIT_RC='0', PKG_TEST_RC='',
                   PKG_PROVIDES='', PKG_BATCH_FAIL='', PKG_REPO_FAIL='', PKG_NO_CHANGE='')
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (('MANIFEST', self.manifest), ('INSTALL_SH', self.script), ('DB_LOCK', self.lock)):
            mock = patch.object(p, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.exe('pacman')
        self.exe('sudo')
        self.exe('systemctl')
        # Only the fake install script is ever passed to this real interpreter.
        (self.bin / 'bash').symlink_to(shutil.which('bash', path=os.defpath))

    def exe(self, name):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\n' + FAKE)
        path.chmod(0o755)
        return path

    def manifest_for(self, text):
        self.manifest.write_text(text)
        return p.load()

    def installed(self, *names):
        self.db.write_text(json.dumps(list(names)))

    def calls(self, name=None):
        rows = [json.loads(s) for s in self.log.read_text().splitlines()] if self.log.exists() else []
        return [r for r in rows if name is None or r[0] == name]

    def test_load_validation(self):
        good = '[[set]]\nid="one"\ntitle="One"\ngroup="apps"\n'
        self.manifest_for(good)
        self.assertFalse(p.load()[0].removable)
        cases = [
            (good.replace('id="one"\n', ''), 'id'),
            (good.replace('title="One"\n', ''), 'title'),
            (good.replace('group="apps"\n', ''), 'group'),
            (good.replace('one', 'bad_id'), 'id'),
            (good.replace('apps', 'oops'), 'group'),
            (good + good, 'id'),
            (good.replace('apps', 'base') + 'removable=true\n', 'removable'),
            (good.replace('apps', 'theme') + 'removable=true\n', 'removable'),
            (good + 'removable=true\nstep="one"\n', 'removable'),
            (good + 'installed_if={bad="x"}\n', 'installed_if.bad'),
            (good + 'installed_if={cmd=42}\n', 'installed_if.cmd'),
            (good + 'repo="one"\n', 'repo'),
            (good + 'aur=[42]\n', 'aur'),
            (good + 'any_of=["one"]\n', 'any_of'),
            (good + 'default="yes"\n', 'default'),
            (good + 'step_always=true\n', 'step_always'),
        ]
        for text, field in cases:
            with self.subTest(field=field, text=text):
                self.manifest.write_text(text)
                with self.assertRaisesRegex(ValueError, re.escape(str(self.manifest)) + r': set\[\d+\]\.' + re.escape(field) + ':'):
                    p.load()

    def test_unsatisfied_one_call_and_provides(self):
        self.installed('one')
        os.environ['PKG_PROVIDES'] = 'nodejs'
        self.assertEqual(p.unsatisfied(['one', 'two', 'nodejs', 'two']), {'two'})
        self.assertEqual(self.calls('pacman'), [['pacman', '-T', 'one', 'two', 'nodejs']])
        self.assertEqual(p.unsatisfied(['one', 'nodejs']), set())
        os.environ['PKG_TEST_RC'] = '3'
        with self.assertRaisesRegex(RuntimeError, 'exited 3'):
            p.unsatisfied(['one'])
        (self.bin / 'pacman').unlink()
        self.assertIsNone(p.unsatisfied(['one']))

    def test_status_single_call_all_names_and_alternatives(self):
        sets = [p.PkgSet('basic', 'Basic', repo=['a', 'b']),
                p.PkgSet('firedragon', 'Browser', aur=['firedragon-catppuccin-bin'],
                         any_of=[['firedragon-catppuccin-bin', 'firedragon']]),
                p.PkgSet('clash-verge', 'Proxy', aur=['clash-verge-rev-bin'],
                         any_of=[['clash-verge-rev-bin', 'clash-verge-rev']]),
                p.PkgSet('absent', 'Absent', repo=['c'])]
        self.installed('a', 'firedragon', 'clash-verge-rev')
        states = p.status(sets)
        self.assertEqual(states['basic'], p.SetStatus('partial', ['b']))
        self.assertEqual(states['absent'], p.SetStatus('missing', ['c']))
        for name in ('firedragon', 'clash-verge'):
            self.assertEqual(states[name], p.SetStatus('installed', []))
        self.assertEqual(len(self.calls('pacman')), 1)
        self.assertEqual(set(self.calls('pacman')[0][2:]), set(p._names(sets)))
        self.log.unlink()
        self.assertTrue(all(s.state == 'installed' for s in p.status(sets, unsat=None).values()))
        self.assertEqual(self.calls(), [])
        absent = p.status(sets, unsat=set(p._names(sets)))
        self.assertEqual(absent['firedragon'].missing, ['firedragon-catppuccin-bin'])

    def test_installed_if_and_handy_missing_precedence(self):
        handy = p.PkgSet('handy', 'Handy', repo=['fuse2', 'wtype', 'xdotool'],
                         installed_if={'file': '~/.local/opt/handy/Handy.AppImage'})
        self.assertEqual(p.status([handy], unsat=set())['handy'],
                         p.SetStatus('missing', ['file:~/.local/opt/handy/Handy.AppImage']))
        app = self.root / '.local/opt/handy/Handy.AppImage'
        app.parent.mkdir(parents=True)
        app.touch()
        self.assertEqual(p.status([handy], unsat=set())['handy'].state, 'installed')
        s = p.PkgSet('checks', 'Checks', repo=['a', 'b'],
                     installed_if={'cmd': 'probe', 'file': str(app), 'unit': 'test.service'})
        os.environ['PKG_UNIT_RC'] = '1'
        st = p.status([s], unsat={'b'})['checks']
        self.assertEqual(st, p.SetStatus('missing', ['b', 'cmd:probe', 'unit:test.service']))
        self.exe('probe')
        os.environ['PKG_UNIT_RC'] = '0'
        self.assertEqual(p.status([s], unsat=set())['checks'].state, 'installed')
        self.assertIn(['systemctl', 'is-enabled', 'test.service'], self.calls('systemctl'))
        alt = p.PkgSet('alt', 'Alt', installed_if={'file': ['/nonexistent', str(app)]})
        self.assertEqual(p.status([alt], unsat=set())['alt'].state, 'installed')

    def test_cmds_satisfy_package_outside_pacman(self):
        s = p.PkgSet('ts', 'TS', repo=['tree-sitter-cli', 'x'], cmds={'tree-sitter-cli': 'tree-sitter'})
        bin_ = self.root / '.cargo/bin'
        bin_.mkdir(parents=True)
        self.assertEqual(p.status([s], unsat={'tree-sitter-cli', 'x'})['ts'].missing, ['tree-sitter-cli', 'x'])
        (bin_ / 'tree-sitter').write_text('#!/bin/sh\n')
        (bin_ / 'tree-sitter').chmod(0o755)
        self.assertEqual(p.status([s], unsat={'tree-sitter-cli', 'x'})['ts'].missing, ['x'])

    def test_install_only_missing_and_inherited_tty(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\nrepo=["present","missing"]\naur=["aur"]\n')
        self.installed('present')
        self.exe('paru')
        real_run = subprocess.run
        with patch.object(p.subprocess, 'run', wraps=real_run) as run:
            result = p.install(['one'], log=lambda _: None)
        self.assertEqual(result, p.InstallResult(0, [], ''))
        self.assertEqual([r for r in self.calls('pacman') if r[1] == '-S'],
                         [['pacman', '-S', '--needed', '--noconfirm', 'missing']])
        self.assertEqual(self.calls('paru'), [['paru', '-S', '--needed', '--noconfirm', 'aur']])
        for call in run.call_args_list:
            if call.args[0][0] in ('sudo', 'paru'):
                self.assertEqual(call.kwargs, {})
        self.assertEqual(len([r for r in self.calls('pacman') if r[1] == '-T']), 2)

    def test_install_retry_and_repo_fallback(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\nrepo=["a","b"]\naur=["c"]\n')
        self.exe('paru')
        os.environ['PKG_BATCH_FAIL'] = '1'
        os.environ['PKG_REPO_FAIL'] = 'b'
        self.assertEqual(p.install(['one'], log=lambda _: None).rc, 0)
        self.assertEqual([r[2:] for r in self.calls('pacman') if r[1] == '-S'],
                         [['--needed', '--noconfirm', 'a', 'b'], ['--needed', '--noconfirm', 'a'],
                          ['--needed', '--noconfirm', 'b']])
        self.assertEqual(self.calls('paru')[0][-2:], ['b', 'c'])

    def test_install_alternatives_are_not_reinstalled(self):
        self.manifest_for('[[set]]\nid="firedragon"\ntitle="Browser"\ngroup="apps"\naur=["firedragon-catppuccin-bin"]\nany_of=[["firedragon-catppuccin-bin","firedragon"]]\n')
        self.installed('firedragon')
        self.assertEqual(p.install(['firedragon'], log=lambda _: None).rc, 0)
        self.assertFalse(self.calls('sudo'))
        self.assertTrue(all(r[1] == '-T' for r in self.calls('pacman')))

    def test_steps_manifest_order_skip_installed_and_always(self):
        self.manifest_for('''[[set]]
id="greeter"
title="Greeter"
group="apps"
repo=["a"]
step="greeter"
[[set]]
id="handy"
title="Handy"
group="apps"
repo=["b"]
step="handy"
[[set]]
id="firedragon"
title="Browser"
group="apps"
repo=["c"]
step="firedragon"
step_always=true
[[set]]
id="dev"
title="Dev"
group="dev"
repo=["d"]
step="podman"
step_always=true
''')
        self.installed('a', 'c', 'd')
        self.assertEqual(p.install(['dev', 'firedragon', 'handy', 'greeter'], log=lambda _: None).rc, 0)
        steps = (self.root / 'steps').read_text().splitlines()
        self.assertEqual(steps, ['step:--only handy', 'step:--only firedragon', 'step:--only podman'])

    def test_failure_redetection_and_hints(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\nrepo=["a"]\n')
        # Exit zero without installing: result must follow detection, not the command rc.
        os.environ['PKG_NO_CHANGE'] = '1'
        result = p.install(['one'], log=lambda _: None)
        self.assertEqual((result.rc, result.failed), (1, ['one']))
        self.assertIn('cachy-update', result.message)
        self.assertNotIn('db.lck', result.message)
        self.lock.touch()
        result = p.install(['one'], log=lambda _: None)
        self.assertIn('sudo rm /var/lib/pacman/db.lck', result.message)
        self.assertTrue(self.lock.exists())
        self.assertFalse(any('-Rns' in r for r in self.calls()))

    def test_missing_helper_and_yay_fallback(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\naur=["a"]\n')
        result = p.install(['one'], log=lambda _: None)
        self.assertIn('No AUR helper', result.message)
        self.assertEqual(result.failed, ['one'])
        self.exe('yay')
        self.assertEqual(p.install(['one'], log=lambda _: None).rc, 0)
        self.assertEqual(len(self.calls('yay')), 1)

    def test_remove_refusal_sharing_and_confirmation(self):
        self.manifest_for('''[[set]]
id="core"
title="Core"
group="base"
repo=["shared"]
[[set]]
id="nvim"
title="Nvim"
group="apps"
repo=["shared","editor"]
removable=true
''')
        self.installed('shared', 'editor')
        self.assertEqual(p.remove(['core'], log=lambda _: None).rc, 1)
        self.assertEqual(self.calls(), [])
        result = p.remove(['nvim'], log=lambda _: None)
        # The protected shared dependency leaves the set partial, hence failed removal.
        self.assertEqual(result.failed, ['nvim'])
        self.assertEqual([r for r in self.calls('pacman') if r[1] == '-Rns'], [['pacman', '-Rns', 'editor']])
        self.assertNotIn('--noconfirm', self.calls('sudo')[0])
        self.assertEqual(json.loads(self.db.read_text()), ['shared'])

    def test_remove_exact_names_only_and_success(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\nrepo=["real","virtual"]\nremovable=true\n')
        self.installed('real')
        self.assertEqual(p.remove(['one'], log=lambda _: None), p.InstallResult(0, [], ''))
        self.assertEqual([r for r in self.calls('pacman') if r[1] == '-Qq'], [['pacman', '-Qq', 'real', 'virtual']])
        self.assertEqual([r for r in self.calls('pacman') if r[1] == '-Rns'], [['pacman', '-Rns', 'real']])

    def test_unknown_ids_rejected_before_commands(self):
        self.manifest_for('[[set]]\nid="one"\ntitle="One"\ngroup="apps"\n')
        for action in (p.install, p.remove):
            with self.assertRaisesRegex(ValueError, r'unknown set\(s\): yakuake'):
                action(['yakuake'])
        self.assertEqual(self.calls(), [])

    def test_terminal_resolution_order_and_self_contained_cmd(self):
        result = self.root / 'result.json'
        terminals = ['xdg-terminal-exec', 'wezterm', 'kitty', 'foot', 'alacritty', 'konsole']
        prefixes = [[], ['start', '--always-new-process', '--cwd', str(p.REPO), '--'],
                    ['--'], ['--'], ['-e'], ['--separate', '-e']]
        for t in terminals:
            self.exe(t)
        self.exe('chosen')
        os.environ['TERMINAL'] = 'chosen'
        self.assertEqual(p.terminal_argv('install', ['one'], result)[0], 'xdg-terminal-exec')
        (self.bin / terminals[0]).unlink()
        self.assertEqual(p.terminal_argv('install', ['one'], result)[:2], ['chosen', '-e'])
        os.environ['TERMINAL'] = ''
        for t, prefix in zip(terminals[1:], prefixes[1:]):
            with self.subTest(terminal=t):
                argv = p.terminal_argv('install', ['one'], result)
                self.assertEqual(argv[:1 + len(prefix)], [t, *prefix])
                cmd = argv[1 + len(prefix):]
                self.assertEqual(cmd[:2], ['bash', '-c'])
                self.assertIn('cd "$1"', cmd[2])
                self.assertIn('packages install --result "$2" "${@:3}"', cmd[2])
                self.assertEqual(cmd[3:], [sys.executable, str(p.REPO), str(result), 'one'])
                (self.bin / t).unlink()
        self.exe('footclient')
        self.exe('gnome-terminal')  # Never an automatic fallback.
        for value in ('', 'footclient'):
            os.environ['TERMINAL'] = value
            with self.assertRaises(FileNotFoundError):
                p.terminal_argv('install', [], result)
        with self.assertRaises(ValueError):
            p.terminal_argv('bogus', [], result)
        self.assertEqual(self.calls(), [])  # Resolution never launches a terminal.

    def test_terminal_basename_mapping(self):
        for name, options in [('konsole', ['--separate', '-e']), ('gnome-terminal', ['--wait', '--']),
                              ('wezterm', ['start', '--always-new-process', '--cwd', str(p.REPO), '--']),
                              ('kitty', ['--']), ('foot', ['--']), ('alacritty', ['-e']), ('custom', ['-e'])]:
            with self.subTest(name=name):
                path = self.exe(name)
                os.environ['TERMINAL'] = str(path) + ' --user-option'
                argv = p.terminal_argv('remove', ['one'], self.root / 'r')
                self.assertEqual(argv[:2 + len(options)], [str(path), '--user-option', *options])
                self.assertIn('packages remove', argv[4 + len(options)])
                path.unlink()

    def test_result_round_trip_atomic_records_and_poll(self):
        path = self.root / 'result.json'
        self.assertIsNone(p.read_result(path))
        self.assertEqual(p.poll_state(path, 100, 130), 'running')
        self.assertEqual(p.poll_state(path, 100, 131), 'no-start')
        p.write_result(path, {'state': 'running', 'pid': os.getpid()})
        self.assertIsNone(p.read_result(path))
        self.assertEqual(p.poll_state(path, 100, 200), 'running')
        # A reaped child supplies a known dead pid without guessing a pid number.
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        p.write_result(path, {'state': 'running', 'pid': child.pid})
        self.assertEqual(p.poll_state(path, 100, 101), 'cancelled')
        result = p.InstallResult(1, ['one'], 'failure')
        p.write_result(path, result)
        self.assertEqual(json.loads(path.read_text()), {'state': 'done', 'rc': 1, 'failed': ['one'], 'message': 'failure'})
        self.assertEqual(p.read_result(path), result)
        self.assertEqual(p.poll_state(path, 100, 101), 'done')
        self.assertEqual(list(self.root.glob('result.json.*')), [])
        path.write_text('{broken')
        self.assertIsNone(p.read_result(path))
        self.assertEqual(p.poll_state(path, 0, 31), 'no-start')

    def test_write_result_replaces_atomically_and_cleans_up_on_error(self):
        path = self.root / 'result.json'
        p.write_result(path, {'state': 'running', 'pid': os.getpid()})
        with patch.object(p.os, 'replace', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                p.write_result(path, p.InstallResult(0, [], ''))
        self.assertEqual(json.loads(path.read_text())['state'], 'running')
        self.assertEqual(list(self.root.glob('result.json.*')), [])


class ManifestTests(unittest.TestCase):
    def test_frozen_migration_table(self):
        sets = p.load()
        expected = {
            'core': ('base', False, 'python git niri noctalia cachyos-niri-noctalia wezterm xwayland-satellite xdg-desktop-portal-gnome xdg-desktop-portal-gtk gnome-keyring wl-clipboard curl base-devel noto-fonts noto-fonts-emoji adwaita-fonts', ''),
            'shell': ('base', False, 'fish fisher starship eza bat fastfetch ugrep direnv', ''),
            'gtk-theme': ('theme', True, 'adw-gtk-theme catppuccin-gtk-theme-mocha', ''),
            'fonts': ('theme', True, 'inter-font ttf-jetbrains-mono-nerd', ''),
            'icons': ('theme', True, '', 'tela-circle-icon-theme-dracula'),
            'qt-theme': ('theme', True, 'kvantum', 'qt6ct-kde'),
            'cursors': ('theme', True, '', 'catppuccin-cursors-mocha'),
            'sassc': ('theme', False, 'sassc', ''),
            'greeter': ('apps', True, 'greetd greetd-regreet plymouth', ''),
            'handy': ('apps', True, 'fuse2 wtype xdotool', ''),
            'firedragon': ('apps', True, '', 'firedragon-catppuccin-bin'),
            'nvim': ('apps', True, 'neovim git ripgrep fd lazygit tree-sitter-cli unzip', ''),
            'micro': ('apps', True, 'micro', ''),
            'telegram': ('apps', True, 'telegram-desktop', 'tg-ws-proxy-bin'),
            'filezilla': ('apps', True, 'filezilla', ''),
            'clash-verge': ('apps', True, '', 'clash-verge-rev-bin'),
            'spotify': ('apps', True, '', 'spotify-launcher'),
            'dev': ('dev', True, 'nodejs npm openssh k9s podman podman-compose passt fuse-overlayfs', ''),
        }
        self.assertEqual([s.id for s in sets], list(expected))
        for s in sets:
            group, default, repo, aur = expected[s.id]
            self.assertEqual((s.group, s.default, s.repo, s.aur), (group, default, repo.split(), aur.split()))
            self.assertEqual(s.step_always, s.id in ('firedragon', 'dev'))
            self.assertEqual(s.removable, s.id in ('nvim', 'micro', 'telegram', 'filezilla', 'clash-verge', 'spotify'))
        self.assertEqual({s.id: s.any_of for s in sets if s.any_of}, {
            'firedragon': [['firedragon-catppuccin-bin', 'firedragon']],
            'clash-verge': [['clash-verge-rev-bin', 'clash-verge-rev']]})
        self.assertEqual({s.id: s.step for s in sets if s.step}, {
            'greeter': 'greeter', 'handy': 'handy', 'firedragon': 'firedragon', 'dev': 'podman'})
        self.assertEqual({s.id: s.installed_if for s in sets if s.installed_if}, {
            'greeter': {'unit': ['greetd.service']},
            'handy': {'file': ['~/.local/opt/handy/Handy.AppImage', '~/.config/autostart/Handy.desktop']}})
        self.assertEqual({s.id: s.cmds for s in sets if s.cmds}, {
            'nvim': {'tree-sitter-cli': 'tree-sitter'}, 'telegram': {'tg-ws-proxy-bin': 'tg-ws-proxy'}})

    def test_current_recipe_and_installer_packages_are_covered(self):
        names = {n for s in p.load() for n in s.repo + s.aur}
        def recipe_names(data):
            for ns in data.get('packages', {}).values():
                yield from ns
            for variant in data.get('variants', {}).values():
                yield from recipe_names(variant)
        for file in (p.REPO / 'mokka/components').glob('*.toml'):
            data = tomllib.loads(file.read_text())
            self.assertFalse(set(recipe_names(data)) - names, file.name)
        # Freeze the former installer lists independently of future install.sh edits.
        former = '''python git niri noctalia cachyos-niri-noctalia wezterm fish fisher starship eza bat fastfetch ugrep direnv
        adw-gtk-theme inter-font catppuccin-gtk-theme-mocha ttf-jetbrains-mono-nerd adwaita-fonts noto-fonts noto-fonts-emoji
        xwayland-satellite xdg-desktop-portal-gnome xdg-desktop-portal-gtk gnome-keyring wl-clipboard curl nodejs npm
        base-devel telegram-desktop filezilla openssh k9s podman podman-compose passt fuse-overlayfs
        greetd greetd-regreet plymouth fuse2 wtype xdotool tela-circle-icon-theme-dracula clash-verge-rev-bin tg-ws-proxy-bin spotify-launcher'''
        self.assertFalse(set(former.split()) - names)


if __name__ == '__main__':
    unittest.main()
