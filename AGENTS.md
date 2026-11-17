# AGENTS.md

Notes for AI agents (Claude Code, Codex, …) working on this repo. Read this first, then [README.md](README.md) for
the user-facing view.

## What this is

A desktop setup for CachyOS/Arch: **niri** (compositor) + **Noctalia** (shell, bar, colors), with the
**Garuda Linux Mokka** look (Catppuccin Mocha Mauve) ported on top. Two layers, kept strictly apart:

- **Packages** (`mokka/packages.toml`, `rice/packages.py`): install/remove software. Sudo, pacman/AUR, always in a
  terminal the user sees.
- **Appearance** (`mokka/components/*.toml`, `rice/recipe.py`): config files and Noctalia settings only. No sudo,
  never installs packages; a recipe declares `needs = [<set id>]` instead.

`install.sh` runs packages first, then `python -m rice apply --defaults`. The GUI (`rice/gui.py`) shows both layers.

## Ground rules

1. **Upstream is the source of truth.** Garuda/Catppuccin files are fetched at install time into
   `~/.cache/niri-noctalia-rice/upstream`, pinned in `mokka/upstream.lock.toml`, and patched by recipe steps.
   Never copy upstream files into this repo. Change behavior with a recipe step instead.
2. **Pins** come from a release/tag or from what Garuda's PKGBUILD pins. If nothing pins it, track the branch Garuda
   itself uses. `python -m rice lock --latest` / `--pkgbuilds <commit>` re-pins; `python -m rice update` shows the diff.
3. **Noctalia drives colors.** Don't hard-code colors that Noctalia templates generate (GTK, Qt palette, WezTerm,
   niri). Kvantum/Colloid are the only static themes.
4. **The user's files win.** Hand-edited files are kept unless `--force`. Shared configs are patched key by key
   (`ini_set`, `toml_set`, `json_set`, `replace`). `~/.config/rice-custom/` is never written over, never tracked.
5. **Sudo only in a visible terminal.** Never run sudo from the GUI process or hidden.
6. **No personal data in the repo:** no usernames, hostnames, emails, absolute home paths (`@HOME@`/`$HOME` instead),
   personal env vars, tokens. Personal fish lines go to `~/.config/rice-custom/fish/`.
7. **Tests never touch the live session:** no `noctalia msg config-set` / `color-scheme-set`, no writes to the real
   `$HOME`. Use the fixtures and `--no-run`.
8. **Git:** commit or push only when the user asks. Commit author is the repo's configured identity.
9. **Big features** go through spec → plan → critic/architect review → verifier → implementation (see `docs/mokka/`).
   Small fixes don't need it.

## Layout

| Path | What |
|---|---|
| `install.sh` | Installer. `--only <step>` steps (greeter, handy, firedragon, podman) are at the top and never call rice |
| `rice/cli.py` | `python -m rice …`: lock, fetch, apply, disable, status, update, owned, refresh, packages, gui |
| `rice/recipe.py` | Recipe loader and the 9 ops; schema in the module docstring |
| `rice/state.py` | State file `~/.local/state/niri-noctalia-rice/state.json` (owned files, hashes, run_pending) |
| `rice/fetch.py`, `rice/lock.py` | Download/cache upstream sources, read/write the lock |
| `rice/palette.py` | Generates the Noctalia `Mokka.json` palette from Garuda colors |
| `rice/packages.py` | Package sets: load, detect (`pacman -T`), install/remove, terminal pick, result file |
| `rice/gui.py` | GTK4/libadwaita GUI, Appearance and Packages tabs. Pure helpers at the top are unit-tested |
| `mokka/components/*.toml` | One recipe per component |
| `mokka/packages.toml` | Package sets (`repo`, `aur`, `any_of`, `cmds`, `installed_if`, `step`) |
| `mokka/upstream.lock.toml` | Pinned upstream URLs + sha256 |
| `mokka/files/` | Our own variant files (GTK CSS, Starship, Fastfetch preset) |
| `mokka/previews/`, `mokka/capture-previews` | GUI preview images and the script that captures them |
| `mokka/custom-skel/` | Examples copied into `~/.config/rice-custom/` when missing |
| `home/` | Base configs copied by `install.sh` (niri, Noctalia, WezTerm, GTK, fish conf.d). `@HOME@` in the Noctalia config is replaced with `$HOME` |
| `etc/` | System files: greetd/ReGreet, initrd mount timeout |
| `nvim/`, `agents/` | Sources for the `nvim` and `agents` recipes |
| `docs/mokka/` | SPEC/PLAN (v1) and SPEC-2/PLAN-2 (packages vs appearance split) with review notes |
| `tests/` | stdlib `unittest`; fixtures in `tests/fixtures/` |

## Recipes

A recipe (`mokka/components/<id>.toml`) has `id`, `title`, `description`, `group`, `default`, `sources` (lock ids),
`needs` (package set ids), `previews`, `steps`, optional `off_steps`, `variants.<name>.steps` + `default_variant`,
`conflicts`, `requires`.

Ops (details in `rice/recipe.py`):

- `copy` `src="<lock-id>:<path>"|"repo:<path>"`, `dst=<path under ~>`, `track=false` for seed files copied once.
- `generate` (named generator, e.g. `noctalia_palette`), `run` (`when="apply"|"build"`).
- `noctalia`: `color_scheme=[…]` or `list_add`/`list_remove` on a dotted path. Goes through `noctalia msg` IPC; reads
  first, writes only if different; needs Noctalia running, otherwise stays `run_pending`.
- Patches: `replace`, `drop_lines`, `ini_set`, `toml_set`, `json_set`. A `target` is a path under `~` or
  `<lock-id>:<path>` (patches the source copy before hashing).

A component with patch steps needs `off_steps` to be reversible. Add a test in `tests/test_recipes_data.py` when you
add or change a recipe.

## Packages

A set is satisfied when every `repo`/`aur` name passes one `pacman -T` call (provides are resolved), or an `any_of`
alternative is present, or the `cmds` command exists (PATH plus `~/.local/bin`, `~/.cargo/bin`), and every
`installed_if` kind (`cmd`/`file`/`unit`; a list means "any of") passes. Detection must stay offline and fast:
no per-package calls. Install runs `pacman -S --needed`, falls back to paru/yay, then the set's `install.sh --only`
step. Never `-Syu`.

## Things that bite

- **Noctalia 5.x layering:** `~/.config/noctalia/config.toml` is the base; `~/.local/state/noctalia/settings.toml`
  (written by the UI and `config-set`) overrides it. Editing config.toml alone may do nothing; use the `noctalia` op.
  `noctalia config validate` lists legacy keys. `noctalia theme --list-templates` shows builtin vs community template
  ids (starship, bat, neovim are community).
- **niri:** includes are ordered; later binds override earlier ones. `rice.kdl` is included before `noctalia.kdl`,
  `../rice-custom/niri.kdl` last with `optional=true`. Always run `niri validate` after editing KDL. niri reloads its
  config on save. Window rules live in `home/.config/niri/cfg/rules.kdl` (e.g. the Handy `Recording` overlay).
- **Live vs repo:** fixing a file in `home/` does not change the user's live copy if rice owns it.
  `python -m rice refresh <relpath>` restores it from the repo (with a backup) and re-applies patches.
- **GTK4 `settings.ini`** must not contain GTK3-only keys (`gtk-button-images`, `gtk-menu-images`, `gtk-modules`).
- **Terminal for installs** is resolved in `packages.terminal_argv`; never assume WezTerm.
- `pkill -f <pattern>` from an agent shell can match and kill the agent's own shell; kill by PID.

## Commands

```bash
python -m unittest discover -s tests          # all tests (stdlib only)
python -m rice status                         # components; --remote checks upstream
python -m rice apply --defaults --dry-run     # preview, no network/builds
python -m rice apply <id> --yes --no-run      # write files, skip run/noctalia steps
python -m rice packages status
python -m rice gui --smoke                    # GUI smoke test (needs a display)
./install.sh --yes --no-pkgs --print-plan     # installer plan, no writes, no sudo
niri validate
noctalia config validate
```

## Where to look upstream

- Garuda Mokka: https://gitlab.com/garuda-linux/themes-and-settings/settings/garuda-mokka
- Garuda PKGBUILDs (fish config, starship prompt, pins): https://gitlab.com/garuda-linux/pkgbuilds
- Garuda wallpapers: https://gitlab.com/garuda-linux/themes-and-settings/artwork/garuda-wallpapers
- Catppuccin ports: https://github.com/catppuccin (kde, konsole, bat, micro, grub, cursors)
- Colloid GTK: https://github.com/vinceliuice/Colloid-gtk-theme
- niri wiki (config, window rules, IPC): https://github.com/YaLTeR/niri/wiki
- Noctalia: https://github.com/noctalia-dev/noctalia-shell and `noctalia --help`, `noctalia msg --help`
- CachyOS wiki: https://wiki.cachyos.org/
- Handy: https://github.com/cjpais/Handy
