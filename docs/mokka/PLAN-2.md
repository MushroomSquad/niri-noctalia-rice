# PLAN-2: Packages layer, appearance `needs`, GUI rework

Status: pending approval (v2, after architect SOUND-WITH-CHANGES and critic APPROVE-WITH-FIXES)

This plan implements `docs/mokka/SPEC-2.md` and builds on PLAN.md §2, §3 and §12. The engine semantics stay as they
are: ownership, `run_pending`, the `noctalia` op and the custom dir. Mode: ralplan SHORT.

User requirements added in review:
- **U1.** Installs work with whatever terminal the user has.
- **U2.** Old state from earlier versions never crashes rice.

## 0. SPEC-2 §5 open questions: resolved defaults

| # | Question | Decision |
|---|----------|----------|
| 1 | Manifest format | One file, `mokka/packages.toml`, with `[[set]]` tables. |
| 2 | Base sets in the GUI | Shown under a collapsed **Base system** expander. The only action is **Install missing**. There is never a Remove. |
| 3 | What `needs` refers to | Package-set ids only. Theme packages get small sets: `qt-theme`, `cursors`, `fonts`, `gtk-theme`, `sassc`. |
| 4 | Terminal run and result | rice writes one result file `F`: `{"state":"running", pid}` when it starts, replaced by `{"state":"done", rc, failed, message}` when it ends. The GUI polls `F` and checks whether the pid is alive. It never reads the terminal's rc or `proc.poll()` (§2.4). The terminal is resolved per U1. |
| — | yakuake | Dropped. The recipe and its README row are deleted. |
| — | Recipes that only installed packages or ran `install.sh --only` (`base-packages`, `greeter`, `handy`, `firedragon`) | Deleted and turned into package sets. `mokka/previews/base-packages` is renamed to `core`. The other preview dirs already match their set ids. |

Not created: `.omc/plans/open-questions.md`. The caller limited output to this file. The residual questions are in §7.

## 1. RALPLAN-DR summary

**Principles**
1. The live system is the truth for packages. One `pacman -T` call over all names resolves `provides`, plus optional
   cmd/file/unit checks. Nothing about packages is read from `state.json`.
2. There is one manifest. install.sh, the CLI and the GUI all read `mokka/packages.toml`.
3. Sudo runs only in a visible terminal or the user's own TTY. rice never removes pacman's lock.
4. Minimal code. Stdlib and PyGObject only, one new module, no plugin or registry layers.
5. The rice engine (PLAN §2/§12) is untouched apart from replacing `packages` with `needs`. Stale state is tolerated (U2).

**Top-3 decision drivers**
1. Detection accuracy. This is the user's main complaint: greeter, Handy and FireDragon show as "off" or "missing".
2. A single source for package lists, with no duplication between install.sh and the recipes.
3. GUI clarity. Two tabs, a state on every card, explicit install actions, and any terminal (U1).

**Options: layer placement**

| Option | Pros | Cons |
|---|---|---|
| **A (chosen):** new `rice/packages.py` plus `mokka/packages.toml`. Recipes declare `needs`. install.sh calls `rice packages …`. | Clean split between the layers. Live, fast detection (one `pacman -T`). T1 is a new file, so it can be built in parallel. | install.sh must bootstrap python before phase 1. Its blocks are reordered and the `--only` steps move into functions. |
| B: keep packages in recipes as "apps" components that only have `packages`. | Least new code. | The layers stay mixed. Status stays tied to `state.json`, which is the root bug. No way to express detection or alternatives. |
| C: a bash-sourceable `mokka/packages.sh` that rice parses. | install.sh needs no python. | rice would have to parse bash. No titles or detection rules. The GUI metadata would live elsewhere. |

**Options: install completion protocol**

| Option | Pros | Cons |
|---|---|---|
| D0: use the terminal process's exit code or `proc.poll()`. | No files. | Broken, measured here. `wezterm start` without `--always-new-process` hands off in about 90 ms. With that flag it waits, but still returns rc 0. `wezterm -e` is an alias for `start`. `xdg-terminal-exec` and server-mode terminals also hand off. Fails U1. |
| D1: a shell wrapper, `sh -c 'cmd; echo $? > F'`. | Trivial. | Nothing is written when the user closes the window, so "cancelled" and "still running" look the same. |
| **D2 (chosen):** one result file `F` with a liveness record. `{"state":"running", pid}` is written at start and replaced by `{"state":"done", rc, failed, message}` in `finally`. The GUI polls `F` and checks whether the pid is alive. `failed` comes from re-detection. | Works with any terminal (U1). Tells done, running, cancelled and did-not-start apart. One file. Truth comes from re-detection. | About 30 lines, plus a pure `poll_state()` that tests can cover. |

**Why the others are out**
- B fails driver 1 (detection stays on the state file).
- C fails principle 4 (a bash parser inside python) and driver 3 (no metadata).
- D0 and D1 cannot tell cancelled from running, and break with handoff terminals, so they fail U1.
- Streaming and regex-scanning pacman's output was dropped. It would need a pipe, which breaks the TTY, and it
  duplicates what re-detection already gives.

## 2. Data model

### 2.1 `mokka/packages.toml`
```toml
[[set]]
id = "firedragon"                 # required, unique, [a-z0-9-]+
title = "FireDragon browser"      # required
description = "Replaces Firefox; Mod+B opens FireDragon."
group = "apps"                    # required: base | theme | apps | dev
default = true                    # preselected in install.sh (base sets are always installed)
repo = []                         # pacman packages
aur = ["firedragon-catppuccin-bin"]
any_of = [["firedragon-catppuccin-bin", "firedragon"]]  # only where no package `provides` the listed name
step = "firedragon"               # optional: `bash install.sh --only <step>` after the packages
installed_if = { cmd = "firedragon" }  # optional extra checks, all must hold: cmd | file | unit
removable = true                  # optional, default false; group apps|dev only, never with a step
```
- `installed_if` keys:
  - `cmd`: checked with `shutil.which`;
  - `file`: a path, `~` is expanded;
  - `unit`: `systemctl is-enabled <unit>` returns 0.
- No `post` or `remove` scripts. Two `--only` steps take over that work:
  - FireDragon's "remove firefox and rebind Mod+B" becomes `install.sh --only firedragon`;
  - podman's subuid/subgid lines become `--only podman`.
- `packages.load()` raises `ValueError("<file>: set[<i>].<field>: …")` on any of:
  - a missing required field;
  - an unknown group;
  - a duplicate id;
  - `removable` set on a base set or on a set that has a step;
  - an unknown `installed_if` key.

**Migration table.** The executor copies this exactly. Every former `REPO_PKGS`, `AUR_PKGS` and recipe `packages`
entry lands in some set.

| id | group | default | repo | aur | step / installed_if / any_of |
|---|---|---|---|---|---|
| core | base | – | python git niri noctalia cachyos-niri-noctalia wezterm xwayland-satellite xdg-desktop-portal-gnome xdg-desktop-portal-gtk gnome-keyring wl-clipboard curl base-devel noto-fonts noto-fonts-emoji adwaita-fonts | | |
| shell | base | – | fish fisher starship eza bat fastfetch ugrep direnv | | |
| gtk-theme | theme | true | adw-gtk-theme catppuccin-gtk-theme-mocha | | |
| fonts | theme | true | inter-font ttf-jetbrains-mono-nerd | | |
| icons | theme | true | | tela-circle-icon-theme-dracula | |
| qt-theme | theme | true | kvantum | qt6ct-kde | |
| cursors | theme | true | | catppuccin-cursors-mocha | |
| sassc | theme | false | sassc | | |
| greeter | apps | true | greetd greetd-regreet plymouth | | step greeter; unit greetd.service |
| handy | apps | true | fuse2 wtype xdotool | | step handy; file ~/.local/opt/handy/Handy.AppImage |
| firedragon | apps | true | | firedragon-catppuccin-bin | step firedragon; any_of [firedragon-catppuccin-bin, firedragon] |
| nvim | apps | true | neovim git ripgrep fd lazygit tree-sitter-cli unzip | | removable |
| micro | apps | true | micro | | removable |
| telegram | apps | true | telegram-desktop | tg-ws-proxy-bin | removable |
| filezilla | apps | true | filezilla | | removable |
| clash-verge | apps | true | | clash-verge-rev-bin | removable; any_of [clash-verge-rev-bin, clash-verge-rev] |
| spotify | apps | true | | spotify-launcher | removable |
| dev | dev | true | nodejs npm openssh k9s podman podman-compose slirp4netns fuse-overlayfs | | step podman |

- `git` is in both `core` and `nvim`. That is harmless with `--needed`. Remove skips any name that another installed
  set lists.
- Bootstrap literals stay in install.sh as a documented exception: `python cachy-update libnotify` and the paru build.

### 2.2 Detection rules
- `packages.unsatisfied(names)` makes **one** call: `pacman -T <names…>`.
  - rc 0: returns an empty set.
  - rc 127: returns the set of printed lines, which are the unsatisfied names.
  - Any other rc raises `RuntimeError`.
  - pacman is absent: returns `None`.
  - `pacman -T` resolves `provides`, so for example `nodejs-lts-*` satisfies `nodejs`.
  - Status queries all names of all sets (and all `any_of` members) in that single call.
- `None` means package checks pass. This keeps today's behaviour on non-Arch hosts. Tests inject `unsat` explicitly.
- A listed name counts as present when:
  - it is not in `unsat`; or
  - it belongs to an `any_of` group that has some member not in `unsat`.
- `missing(set)` is made of:
  - the listed names that are not present;
  - plus `"unit:<u>"`, `"cmd:<c>"` and `"file:<f>"` for each failed `installed_if` check.
- State, first match wins:
  1. any `installed_if` check fails → **missing**. The set's step has not run. Example: **Handy**, whose
     fuse2/wtype/xdotool are all present but whose AppImage file is absent, is `missing`, not `partial`.
  2. `missing == []` → **installed**.
  3. some listed names are present → **partial**.
  4. otherwise → **missing**.

### 2.3 Recipes declare `needs`
- `needs = ["qt-theme"]` sits at the top level, and `variants.<n>.needs` per variant. The effective needs are the
  top-level list ∪ the chosen variant's list, ordered and unique.
- `recipe.load()` rejects a `packages` key with the error
  `packages: moved to mokka/packages.toml, use needs = [set ids]`.
- Mapping:

  | Recipe | needs |
  |---|---|
  | qt | `qt-theme` |
  | cursor | `cursors` |
  | fonts | `fonts` |
  | gtk, variant noctalia | `gtk-theme` |
  | gtk, variant colloid | `sassc` |
  | fish, bat, starship, fastfetch | `shell` |
  | micro | `micro` |
  | nvim | `nvim` |

  `only_if_installed` (konsole-kate) stays as it is.
- `APPS_RUN` is deleted.
- **Old state (U2, A11).** The live `state.json` here has no entries for deleted recipes, but other machines may have
  them (yakuake, greeter, firedragon, base-packages). These entries are **not pruned**:
  - they stay in `state.json` untouched;
  - `owned()` keeps listing their paths, which is the `install_tree` protection;
  - `status()`, `apply --defaults` and `_select` iterate recipes, so they ignore those entries and `status()` has no
    key for them;
  - `disable()` returns `Result("failed", message="unknown component <id>")` for any id with no recipe.
- `--no-packages` stays for one release as a hidden argparse no-op (`help=SUPPRESS`). An old install.sh may run against
  the new rice when `git pull` fails.

### 2.4 Terminal-run protocol
1. **Terminal resolution (U1, A10).** `terminal_argv()` takes the first hit:

   | # | Terminal | argv |
   |---|---|---|
   | 1 | `xdg-terminal-exec` | `xdg-terminal-exec CMD…` |
   | 2 | `$TERMINAL` | if its basename matches a row below or `gnome-terminal`, that row's form; otherwise `$TERMINAL -e CMD…` |
   | 3 | wezterm | `wezterm start --always-new-process --cwd REPO -- CMD…` |
   | 4 | kitty | `kitty -- CMD…` |
   | 5 | foot | `foot -- CMD…` (never `footclient`) |
   | 6 | alacritty | `alacritty -e CMD…` |
   | 7 | konsole | `konsole --separate -e CMD…` |
   | — | gnome-terminal | `gnome-terminal --wait -- CMD…`, reachable only through `$TERMINAL` or xdg |

   If none is found, `terminal_argv()` raises `FileNotFoundError`. The GUI shows the toast "No terminal found: set
   $TERMINAL or install xdg-terminal-exec".
2. **CMD.** A handed-off command runs with the terminal server's environment and cwd, so CMD is self-contained:
   ```
   ["bash", "-c",
    'cd "$1" && "$0" -m rice packages <action> --result "$2" "${@:3}"; read -rp "Press Enter to close" _',
    sys.executable, REPO, F, *ids]
   ```
   `sys.executable` is absolute.
3. **Result file.** `F = $XDG_RUNTIME_DIR/rice-pkg-<uuid>.json`, with a `tempfile` fallback. There is one file, and it
   is always replaced atomically (tmp plus `os.replace`).
   - With `--result F`, the CLI first writes `{"state": "running", "pid": os.getpid()}`.
   - It runs pacman, the helper and the steps **inline with the inherited TTY**. There is no pipe, so sudo prompts in
     place.
   - In `finally` it replaces `F` with `{"state": "done", "rc": …, "failed": […], "message": …}`.
4. **Result.** `failed` is the set of requested ids whose state is not `installed` after re-detection with
   `pacman -T`. `rc = 1 if failed else 0`. When `rc != 0`, `message` gets these hints:
   - always: "If a download failed (404), the package database is stale: run cachy-update first, then retry."
   - if `/var/lib/pacman/db.lck` exists: "Check that no pacman is running, then `sudo rm /var/lib/pacman/db.lck`."
     rice never removes the lock itself.

   For remove, `failed` is the requested ids still `installed` or `partial`.
5. **GUI polling.** `packages.poll_state(F, launched_at, now)` is pure and reads only the file. The GUI calls it from
   `GLib.timeout_add` every 500 ms:

   | Condition | State | GUI action |
   |---|---|---|
   | `F.state == "done"` | `done` | read the result, toast, delete `F` |
   | `F.state == "running"` and the pid is alive (`os.kill(pid, 0)`) | `running` | wait |
   | `F.state == "running"` and the pid is dead | `cancelled` | toast "Cancelled" |
   | `F` missing and `now - launched_at > 30` s | `no-start` | toast "Terminal did not start"; the argv goes to the Log |
   | `F` missing, under 30 s | `running` | wait |

   Every final state queues a refresh (§4.5). The GUI never uses `proc.poll()` or the terminal's rc (D0). The launcher
   is started with `subprocess.Popen(..., start_new_session=True)` and then forgotten.
6. **Install order.** Only names that `-T` reports as unsatisfied are installed (`any_of` included).
   1. One `sudo pacman -S --needed --noconfirm <missing repo names>`. If it fails, retry each package on its own.
   2. Failed repo names plus missing AUR names go to `paru|yay -S --needed --noconfirm`.
   3. For each requested set that has a step, in manifest order, run `bash {REPO}/install.sh --only <step>`. **A set
      whose state was already `installed` before the run is skipped (A11)**, so `./install.sh update` does not redo the
      display-manager switch.
   4. Re-detect (point 4).
7. **Remove.** Allowed for `removable` sets only.
   - It removes the set's names that `pacman -Qq <names>` reports as installed by that name, minus names that another
     installed set lists.
   - The command is `sudo pacman -Rns <names>`, **without** `--noconfirm`. pacman's own prompt is the second
     confirmation, after the GUI dialog.

## 3. Frozen API contract

Frozen **before T1**. T1 and T2 implement it. T3 and T4 code against it from the start.

```python
# rice/packages.py  (new)
MANIFEST = REPO / "mokka/packages.toml"; INSTALL_SH = REPO / "install.sh"
GROUPS = ("base", "theme", "apps", "dev")
@dataclass class PkgSet: id; title; description; group; default: bool; repo: list[str]; aur: list[str]
    any_of: list[list[str]]; step: str|None; installed_if: dict; removable: bool
@dataclass class SetStatus: state: str          # installed|partial|missing
    missing: list[str]                          # names and "unit:/cmd:/file:" tokens
@dataclass class InstallResult: rc: int; failed: list[str]; message: str
load(path=MANIFEST) -> list[PkgSet]                        # validated, manifest order
unsatisfied(names: Iterable[str]) -> set[str] | None       # one `pacman -T`; None if pacman absent
status(sets=None, unsat=<computed>) -> dict[str, SetStatus]  # one unsatisfied() call when unsat not given
install(ids, log=print) -> InstallResult                   # inline, inherited TTY; installed sets are no-ops
remove(ids, log=print) -> InstallResult                    # removable sets only; else rc=1 + message
terminal_argv(action: str, ids: list[str], result: Path) -> list[str]  # action in {"install","remove"}; U1 order
write_result(path, r) -> None; read_result(path) -> InstallResult | None
poll_state(result: Path, launched_at: float, now: float) -> str  # done|running|cancelled|no-start

# rice/recipe.py (changed)
Variant: name; title; needs: list[str]; steps        # `packages` removed
Component: … needs: list[str] …                      # `packages` removed, rest unchanged
needs(c, variant=None) -> list[str]

# rice/state.py Status (changed)
missing_packages -> removed
missing_needs: list[str]    # effective-need set ids not `installed`
run_pending: bool

# rice/cli.py (changed)
apply(ids, variants=None, dry_run=False, force=False, install_needs=False, run=True, log=print) -> dict[str, Result]
    # install_needs: packages.install(<missing needs of the selected comps>) once, inline, before processing.
    # A comp whose needs are still missing -> Result("skipped", message="needs a, b"); nothing written, no state entry.
    # dry_run: same skip, never installs.
disable(ids, log=print)     # an id with no recipe -> Result("failed", message="unknown component <id>")
status(remote=False)        # one packages.status() per call
```
The two existing test call sites of `apply(packages=…)` are renamed mechanically: `test_noctalia.py:109` and
`test_cli.py:184`.

**CLI**
- `rice packages list` prints one TSV line per set, in manifest order:
  `id<TAB>group<TAB>default(0|1)<TAB>step-or-dash<TAB>title`.
- `rice packages status` prints one line per set: `id: installed|not installed|partial (missing: a b)`.
- `rice packages install|remove <ids…> [--result F]`
  - Exits with `rc` and prints `message`.
  - An unknown id, including a stale one such as `yakuake`, exits 2 with `unknown set(s): …`.
- `rice apply` gains `--install-needs`, used by the CLI and the GUI. install.sh does **not** use it.
- `--no-packages` stays as a hidden no-op (`help=SUPPRESS`) for one release (A11).
- Exit code of `apply`:
  - 1 if any result is `failed`;
  - 1 if an id named explicitly (positional or `--with`) was skipped with `needs …`;
  - 0 for needs-skips among the components pulled in by `--defaults`.
- `rice status` replaces the flag `pkg:<name>` with `needs:<set id>` and adds the flag `run-pending`.

## 4. GUI design (`rice/gui.py`, English)

### 4.1 Widget tree
```
Adw.ApplicationWindow (1200x820, title "niri-noctalia-rice")
└ Adw.ToastOverlay
  └ Adw.ToolbarView
    ├ top: Adw.HeaderBar
    │   title_widget: Adw.ViewSwitcher(stack, policy=WIDE)
    │   pack_start: Button(icon view-refresh-symbolic, tooltip "Check for upstream updates")
    │               Button(icon folder-download-symbolic, tooltip "Fetch sources")
    │               Button(icon folder-open-symbolic, tooltip "Open custom folder")
    │   pack_end:   Button "Apply (N)" .suggested-action (insensitive when N=0)
    │               Button "Show changes"
    │               Label summary .dim-label  "N pending · upstream updates: yes|no|not checked"
    ├ content: Adw.ViewStack
    │   page "appearance" (title "Appearance", icon applications-graphics-symbolic), default
    │   page "packages"   (title "Packages",   icon package-x-generic-symbolic)
    │   each page: Gtk.ScrolledWindow > Adw.Clamp(max 1500) > Box(V, spacing 24, margin 24) > sections
    │   section: Box(H, spacing 8){Gtk.Image(group icon), Label(group title, .title-2)}
    │            + Gtk.FlowBox(homogeneous, min 1 / max 3 per line, selection NONE, spacing 18)
    │   Packages "base" section: Gtk.Expander(label "Base system (always installed)", expanded=False) > FlowBox
    └ bottom: Adw.ViewSwitcherBar(stack, reveal when narrow) + Gtk.Expander "Log" > ScrolledWindow(TextView, 140px)
              (the expander opens itself on an error)
```

Group titles and icons are one dict in gui.py:

| Tab | Group | Title | Icon |
|---|---|---|---|
| Appearance | look | Look & palette | `preferences-desktop-appearance-symbolic` |
| Appearance | qt | Qt & KDE apps | `applications-system-symbolic` |
| Appearance | gtk | GTK | `applications-graphics-symbolic` |
| Appearance | shell | Shell | `utilities-terminal-symbolic` |
| Appearance | terminal | Terminal apps | `utilities-terminal-symbolic` |
| Appearance | apps | App configs | `applications-other-symbolic` |
| Appearance | wallpapers | Wallpapers | `preferences-desktop-wallpaper-symbolic` |
| Packages | base | Base system | `computer-symbolic` |
| Packages | theme | Theme packages | `preferences-desktop-theme-symbolic` |
| Packages | apps | Applications | `applications-other-symbolic` |
| Packages | dev | Development | `applications-engineering-symbolic` |

### 4.2 Appearance card (Box V, `.card`, width_request 360)
1. **Preview**, height 220.
   - An `Adw.Carousel` of `Gtk.Picture` with content_fit COVER, plus `Adw.CarouselIndicatorDots` when there is more
     than one picture.
   - A `Gtk.GestureClick` opens the fullscreen preview at the current index.
   - With no pictures it shows a placeholder and the label "Fetch to see preview".
2. **Palette only:** a swatch row under the preview (a homogeneous `Box`, height 28). It is never a carousel page.
3. **Body** (margin 12, spacing 6):
   - the title, `.heading`;
   - the description, `.dim-label`, wrapping, at most 2 lines, then ellipsized;
   - the state line (§4.4);
   - a `Gtk.DropDown` "Variant" when the component has variants;
   - when needs are missing, `Label "Needs: <missing package names>"`, then a `Button "Install packages"`, then the
     caption "Opens a terminal, needs sudo";
   - a footer row with the `Gtk.Switch` "Enabled". Non-reversible components get the caption "Turning off keeps current
     settings". The switch is insensitive while the component is off and its needs are missing.

### 4.3 Package card (same frame)
- **Preview**, height 140, shown when `mokka/previews/<set id>/*.png` exists.
- **Text:** the title, the description, and a package line `.dim-label .caption`: "repo: a b · AUR: c".
- **State line:**
  - **Installed ✓** `.success`;
  - **Not installed** `.dim-label`;
  - **Partially installed: missing a, b** `.warning`.
- **Buttons:**
  - **Install** `.suggested-action` when the set is not installed. On base sets the label is "Install missing".
  - **Remove** `.destructive-action`, only when the set is `removable` and installed. It sits behind an
    `Adw.AlertDialog` with Cancel and Remove: "Remove <title>? This runs pacman -Rns in a terminal."
  - Under the buttons, the caption "Opens a terminal, needs sudo".
  - Every Install and Remove button is insensitive while a terminal run is active. Only one run happens at a time.

### 4.4 State labels
The pure function `appearance_state(c, s, pending) -> (text, css)` returns the first rule that matches:

| # | Condition | Text | CSS |
|---|---|---|---|
| 1 | `pending == "on"` | Will be applied | `.accent` |
| 1 | `pending == "off"` | Will be turned off | `.accent` |
| 2 | `s.app_missing` | App not installed | `.dim-label` |
| 3 | `s.missing_needs` | Needs packages | `.warning` |
| 4 | not `s.enabled` | Off | `.dim-label` |
| 5 | `s.modified` | Hand-edited | `.warning` |
| 6 | `s.run_pending` and the recipe has a `noctalia` step | Waiting for Noctalia | `.warning` |
| 7 | `s.needs_apply is None` | Not built yet | `.dim-label` |
| 8 | `s.needs_apply`, `s.recipe_changed` or `s.stale_sources` | Needs apply | `.accent` |
| 9 | otherwise | Applied ✓ | `.success` |

- Components with missing needs are excluded from the `on` list.
- `N = len(on) + len(off)`.
- The header text comes from the pure function `summary(n, updates: bool|None)`.

### 4.5 Refresh and threading
- `self.cards[id]` keeps handles to the card's widgets:
  - the state label;
  - the needs label and its button;
  - the switch and the dropdown;
  - the install and remove buttons.
- **`refresh()`**
  - If `self.busy` is set, it only sets `self.refresh_wanted = True`.
  - Otherwise it calls `run_bg(lambda: (cli.status(), packages.status()), self.on_refreshed)`.
- **The worker never assigns to `self`.** The current `on_apply` assigns `self.status` inside the worker
  (gui.py:296). That moves to the done callback: `work()` returns `(results_text, status)` and `done` assigns it.
- **`run_bg`'s done wrapper.** It:
  1. clears `busy`;
  2. calls `done(res)`;
  3. if `refresh_wanted` is set, clears it and calls `refresh()`.

  So a refresh asked for during Apply or a terminal run is queued, not lost.
- **`on_refreshed(res)`** stores the statuses, then calls `sync()`.
- **`sync()`** updates labels, buttons and the header in place. The user's pending toggles survive.
- **When `refresh()` is called:**
  - after Apply;
  - after every final `poll_state` (§2.4);
  - after Check for updates, which uses `status(remote=True)` and returns the status to `done`.

### 4.6 Fullscreen preview
- A `Gtk.Window(transient_for=win, modal=True)`:
  - black `.preview-bg`;
  - `fullscreen()`;
  - an `Adw.Carousel` of CONTAIN pictures, starting at the clicked index.
- Esc or a click closes it. The Left and Right keys page through the pictures.

### 4.7 Kept actions and smoke
- **Kept actions:**
  - Check for updates;
  - Fetch;
  - Show changes (the existing `show_text` dialog);
  - Open custom folder;
  - Apply (apply the `on` list, then disable the `off` list, then refresh).
- **Deleted:** the sidebar, the `PreferencesPage`s and the `badges()` string.
- **`--smoke`:**
  - visits both pages;
  - asserts that each page has at least one paintable picture and at least one card;
  - opens and closes the fullscreen preview;
  - exits 0.

## 5. Task breakdown

```
T1 ──> T2 ──┬─> T3 ──┐
            └─> T4 ──┴─> T5
```
- T2 is the recipe migration and the CLI together (A9). Removing `Component.packages` and fixing its readers in
  `cli.py`/`gui.py` has to land atomically. T2's code may be written against §3 in parallel with T1, but it merges
  after T1.
- T3 (install.sh) and T4 (GUI) run in parallel; their files do not overlap.
- Only T2 touches `rice/cli.py`.
- Only T5 touches README.
- No `rice packages install` of a set that has a `step` (greeter, handy, firedragon, dev) may run before T3 lands. The
  new `--only` steps do not exist until then.

### T1: Packages layer (new files only)
- **Files:** `rice/packages.py`, `mokka/packages.toml`, `tests/test_packages.py`.
- **Steps:**
  1. Write the manifest from the §2.1 table.
  2. Implement §3 `packages.*`, including `terminal_argv`, the result file `F` and `poll_state`.
- **Tests** (hermetic): a tmp `bin/` is put first on PATH with fake executables. Each logs its argv to a file.
  - The fake executables are:
    - `pacman`: handles `-T` (prints the names absent from a fixture list and exits 127, else 0), `-Qq`, `-S` and `-Rns`;
    - `sudo`: runs `exec "$@"`;
    - `paru`;
    - `systemctl`;
    - terminal stubs.
  - HOME and XDG_RUNTIME_DIR point to tmp. `INSTALL_SH` is patched to a fake script.
  - The cases are:
    - load errors name the field;
    - `unsatisfied` makes one call and returns `None` when there is no pacman;
    - status installed, partial and missing, and `any_of` (firedragon, clash-verge-rev);
    - **Handy is `missing`** when all its packages are present but the file is absent;
    - `installed_if` cmd, file and unit;
    - install sends only unsatisfied names in one pacman call, retries per package, and sends AUR names to paru;
    - the step runs `--only <step>` only for sets that were not installed;
    - `failed` comes from re-detection;
    - the stale-DB hint appears on failure;
    - the `db.lck` hint appears only when the fake lock path exists. The lock path is a module constant, patched in
      tests;
    - remove refuses a non-removable set, keeps names shared with another installed set, and never passes
      `--noconfirm`;
    - **`terminal_argv`, following the §2.4.1 table:**
      - xdg-terminal-exec beats `$TERMINAL`, which beats wezterm, kitty, foot, alacritty and konsole in turn;
      - a `$TERMINAL` basename such as `konsole` or `gnome-terminal` maps to its row (`--separate`, `--wait --`);
      - an unknown `$TERMINAL` gets `-e`;
      - wezterm has `--always-new-process --cwd REPO`;
      - `footclient` is never used;
      - no terminal raises `FileNotFoundError`;
      - the CMD contains the absolute `sys.executable`, `bash -c` and `cd "$1"`;
    - **`poll_state`**, one case per state, built from fake result files:
      - done;
      - running with a live pid;
      - cancelled with a dead pid;
      - no-start when `F` is missing after more than 30 s;
      - running when `F` is missing under 30 s;
    - the CLI with `--result` writes the `running` record and then the `done` record, including when an exception is
      raised;
    - a set with a step that was already `installed` does not run its step;
    - the result round-trips, and a missing file reads as `None`.
- **Accept:**
  - `python -m unittest tests.test_packages -v` is green.
  - `python -c "from rice import packages; print(len(packages.load()))"` prints `18`.
  - On this machine (read-only), `packages.status()` shows `core`, `shell`, `greeter` and `firedragon` as `installed`.

### T2: Recipe migration and CLI (after T1; the only task that touches `rice/cli.py`)
- **Files:**
  - `rice/recipe.py`, `rice/cli.py`, `rice/state.py`;
  - `rice/gui.py`: a one-line change only, which replaces the `c.packages` badge with `st.missing_needs`;
  - in `mokka/components/`, edit `{qt,cursor,fonts,gtk,fish,bat,starship,fastfetch,micro,nvim}.toml` and delete
    `{base-packages,greeter,handy,firedragon,yakuake}.toml`;
  - `git mv mokka/previews/base-packages mokka/previews/core`;
  - tests: `tests/test_recipe.py`, `tests/test_recipes_data.py`, `tests/test_cli.py`, and `tests/test_noctalia.py`
    (the kwarg rename only).
- **Steps:**
  1. In `recipe.py`, replace `packages` with `needs`, make `load()` reject `packages`, and add `needs()`. Edit the
     recipes per §2.3.
  2. In `cli.py`:
     - delete `_pkgs`, `_have_pkg`, `_missing_packages`, `_install_packages` and `APPS_RUN`;
     - add `_unmet(c, variant, pst)`, backed by one `packages.status()` per `_apply`/`status`;
     - add the `install_needs` flow and the "needs …" skip;
     - change the theme-dir branch to depend on `not dry`;
     - make `disable()` reject unknown ids;
     - add the `packages` subparser and `--install-needs`;
     - add the exit-code rule and the new status flags.
  3. In `state.Status`, swap the fields as in §3.
- **Tests:**
  - `test_recipe.py`: fix the Component literal at line ~161.
  - Data tests:
    - no recipe has `packages`;
    - every `needs` id exists in the manifest;
    - every package in the frozen former lists (written out in the test) appears in some set.
  - CLI tests patch `packages.MANIFEST` and `packages.unsatisfied`:
    - an unmet need gives `skipped` "needs x", with no state entry and no files;
    - `main(["apply","alpha"])` returns 1 on a needs skip, and `--defaults` returns 0;
    - `install_needs=True` calls the mocked `packages.install` once with only the missing ids;
    - a dry run never installs;
    - `missing_needs` and `run_pending` are set;
    - `packages list` prints TSV.
  - **U2 compatibility test (C5, A11):** the fixture `state.json` is
    `{"yakuake":{"whole":{".local/share/yakuake/skins/mocha/x":"…"},"variant":null},"firedragon":{"run_pending":true}}`.
    Check that:
    - `status()`, `apply(--defaults, dry_run)` and `disable(["yakuake"])` don't raise;
    - `disable` returns `Result("failed", …, "unknown component yakuake")`;
    - `owned()` still lists the yakuake path;
    - `status()` has no `yakuake` key;
    - `state.json` is unchanged afterwards (no pruning);
    - `main(["packages","install","yakuake"])` returns 2;
    - `main(["apply","--defaults","--no-packages","--dry-run","--no-run"])` is accepted;
    - `--no-packages` is absent from `--help`.
- **Accept:**
  - `python -m unittest discover -s tests -v` is fully green.
  - `grep -l 'packages' mokka/components/*.toml` prints nothing.
  - `python -m rice status` runs here, and installed needs show no `needs:` flag.

### T3: install.sh (after T2; parallel with T4)
- **Files:** `install.sh`, `tests/test_install_sh.py` (new).
- **Steps:**
  1. **`--only` dispatch at the top (A6).**
     - Immediately after the EUID/pacman checks and the `outs` and `CFG` setup, and before any python call or git pull,
       define `step_greeter`, `step_handy`, `step_firedragon` and `step_podman`. These are the existing greeter, Handy
       and FireDragon blocks and the subuid lines, moved into functions.
     - If `--only` is given, run the named steps, then `exit`.
     - `--only pkgs` exits 1 with "--only pkgs was removed: use python -m rice packages install <ids>".
     - Any other unknown step exits 1 with "unknown --only step".
  2. **Delete `REPO_PKGS` and `AUR_PKGS`.** The main flow never calls the `step_*` functions. They run only via
     `rice packages install`, which runs `install.sh --only <step>`, so nothing runs twice.
  3. **Flags (C6).**
     - These map onto set ids in `OPT[...]`:
       - `--no-greeter` → `greeter`;
       - `--no-handy` → `handy`;
       - `--firedragon` / `--no-firedragon` → `firedragon`;
       - `--nvim` / `--no-nvim` → **both** the `nvim` set and the nvim component (`--with` / `--without nvim`).
     - The generic `--no-<id>` / `--<id>` is validated against `rice packages list` once python is available.
     - These are kept: `--no-pkgs`, `--no-wallpapers`, `--no-mokka`, `--agents` / `--no-agents`, `--gui`,
       `--fish-theme`, `--yes`, `update`.
     - New: `--print-plan`. It prints `sets: <ids>` and `apply: <args>` and exits 0, after the questions and before
       any sudo or file write. Tests use it.
  4. **Main order.**
     1. Bootstrap when pkgs is on: `sudo pacman -S --needed --noconfirm python cachy-update libnotify`, then the
        interactive cachy-update and the paru bootstrap, both unchanged. When pkgs is off, require `command -v python`.
     2. User files: `install_tree`, the seds, the custom seed.
     3. **Phase 1.**
        - Read `python -m rice packages list`.
        - Base sets are always chosen. For the rest, ask `"Install <title>?"` with the manifest default.
        - Run `python -m rice packages install "${chosen[@]}"`. Skip this with `--no-pkgs`.
     4. Post-package user setup: gsettings, the tg-ws-proxy autostart, the cachy-update tray, npm agents, initrd, fish.
     5. The Handy keybind drop, when the `handy` set is not installed.
     6. **Phase 2.** `python -m rice apply --defaults --yes` with `--with` / `--without` for nvim and agents.
        - No `--install-needs` (A2). Components whose needs were declined are skipped, and exit stays 0.
  5. **Closing message (A8).** Add: "Skipped components need packages: install them with `python -m rice packages
     install <ids>` or the GUI Packages tab."
- **Tests:**
  - `bash -n install.sh`;
  - `shellcheck`, skipped if absent;
  - no `REPO_PKGS` or `AUR_PKGS`, and no `pacman -S` literal other than the bootstrap line;
  - the `--only` dispatch block (between the `step_` definitions and its `exit`) contains no `rice`;
  - the manifest `step` values equal the `--only` case names;
  - `--only bogus` exits 1;
  - `--only pkgs` exits 1 with "removed";
  - **one `--print-plan` test per flag:** `--no-greeter`, `--no-handy`, `--firedragon`, `--no-firedragon`, `--nvim`,
    `--no-nvim` (the set is absent **and** `apply` has `--without nvim`), and `--no-pkgs` (`sets:` is empty).
  - The `--print-plan` tests run with `NIRI_RICE_REEXECED=1`, a tmp HOME, `--yes`, and a fake `sudo` that fails if
    it is called.
- **Accept:** `python -m unittest tests.test_install_sh -v` is green.

### T4: GUI rework (after T2; parallel with T3)
- **Files:** `rice/gui.py`, `tests/test_gui_smoke.py`, `tests/test_gui_logic.py` (new).
- **Steps:**
  1. Build §4 exactly.
  2. The pure helpers `appearance_state`, `summary` and `pending` are top-level functions in gui.py.
  3. Polling uses `packages.poll_state`.
- **Tests:**
  - `test_gui_logic` imports `rice.gui` and is skipped without `gi`. It covers:
    - every `appearance_state` branch, and the order between them;
    - the `summary` strings;
    - the refresh queue: with `busy` set, `refresh()` sets `refresh_wanted`, and the done wrapper calls `refresh()`
      once. `run_bg` is driven synchronously through a patched `threading.Thread` and `GLib.idle_add`.
  - The smoke test is extended per §4.7.
- **Accept:**
  - `python -m unittest tests.test_gui_logic -v` is green.
  - Under Wayland, `python -m rice gui --smoke; echo $?` prints `0`.
  - `grep -n 'self.status =' rice/gui.py` shows assignments only in the main-thread callbacks.

### T5: Docs and verification (after T3 and T4)
- **Files:** `README.md`. Update:
  - the install flags, including the C6 mapping and the `--only pkgs` removal;
  - the two phases;
  - the Packages and Appearance tabs;
  - `rice packages`;
  - the terminal resolution order;
  - the yakuake row, which is removed.
- **Accept:** everything in §6 passes.

## 6. Verification plan

### 1. Unit tests (hermetic)
- `python -m unittest discover -s tests -v` is green.
- No test calls the real `pacman`, `sudo`, `systemctl`, `noctalia` or a terminal, and none writes outside tmp.
- Check with `grep -rn '"sudo"\|"pacman"\|wezterm' tests/`: it should match only fake-script creation and asserts.

### 2. CLI on this machine (read-only)
- `python -m rice packages status`:
  - `core`, `shell`, `greeter` and `firedragon` are `installed`;
  - `dev` is `installed` even with `nodejs-lts-*` (provides);
  - handy is `installed` only if its AppImage exists.
- `python -m rice status` shows no `needs:` flag for an installed set.
- `python -m rice apply --defaults --dry-run --no-run` prints only the expected changes.

### 3. GUI smoke
`python -m rice gui --smoke` exits 0, through `tests/test_gui_smoke.py` under `WAYLAND_DISPLAY`.

### 4. Screenshot manual check (M-GUI)
1. Run `python -m rice gui`.
2. Take a shot with `niri msg action screenshot-window` at full width (about 1900 px), and again at about 900 px.
3. Check:
   - **Layout:**
     - 3 columns at full width, 1 or 2 at the narrow size;
     - no horizontal scroll;
     - two tabs;
     - group titles are human-readable and have icons.
   - **Cards:**
     - the palette swatch row sits under the preview with no overlap;
     - every card has a state line;
     - the Install buttons carry the "Opens a terminal, needs sudo" caption.
   - **Header and sections:**
     - the header shows "N pending · upstream updates: …" and "Apply (N)";
     - the Base expander is collapsed.
   - **Preview:** clicking a preview opens it fullscreen, and Esc closes it.
4. Save the shots under `docs/mokka/m-checks/`. Optionally run `oh-my-claudecode:visual-verdict` against this checklist.

### 5. Terminal flow (manual): the status matches re-detection
1. By hand, remove a small removable set such as `micro`. Its Appearance card shows "Needs packages".
2. Click Install. A terminal opens (resolved per §2.4) and sudo prompts in it. Then:
   - the toast reflects `failed` (empty → "Installed");
   - after the refresh, the card and the Packages tab agree with `python -m rice packages status`.
3. Repeat, closing the terminal mid-run.
   - The toast says "Cancelled", and the status again agrees with `packages status`, whatever pacman managed to do.
   - If `db.lck` was left behind, the next run's failure message carries the lock hint.
4. Set `TERMINAL=foot`, or uninstall wezterm on a test box, and check that the flow still completes.

### 6. install.sh
- `bash -n`, shellcheck, and the `--print-plan` flag tests.
- A full run on a fresh VM or container is the user's M-check, not CI.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A name is neither installed nor provided, but an alternative package exists (firedragon, clash-verge-rev). | `any_of`, used only for those cases. `pacman -T` covers `provides` everywhere else. The live M-check in §6.2 catches gaps. |
| The install.sh reorder breaks a fresh install, for example a step writing to a file that `install_tree` later overwrites. | User files come first, then packages and steps, then user setup. Steps are reachable only via `--only`, so they never run twice. A test asserts the dispatch has no `rice`. A full VM run is the user's M-check. |
| A terminal hands off to a server, ignores the cwd, or returns rc 0 regardless (measured: wezterm). | Completion is read only from `F` plus the pid liveness check. CMD does `cd REPO` and uses the absolute `sys.executable`. `no-start` after 30 s gives a toast, the argv in the Log, and a refresh. U1 resolution table with `$TERMINAL` basename mapping (konsole `--separate`, gnome-terminal `--wait`). |
| The user closes the window mid-transaction and leaves `db.lck`. | The result is `cancelled`, and the status comes from re-detection. The failure message carries the lock hint. rice never removes the lock. |
| Old `state.json` entries for deleted recipes on other machines, or an old install.sh calling `--no-packages` after a failed `git pull`. | No pruning: the entries are left untouched and their owned paths keep `install_tree` protection. Iteration is recipe-driven. `disable` and `packages install` reject unknown ids cleanly. `--no-packages` is a hidden no-op for one release. Covered by the U2 test. |
| `./install.sh update` re-runs the greeter step and redoes the display-manager switch. | Steps run only for sets that were not `installed` before the run (§2.4.6). |
| The app sets default to true, so install.sh asks more questions. | Defaults come from the manifest. `--yes` keeps it non-interactive. |

**Residual questions** (defaults applied; revisit after the M-check):
- Should `telegram`, `clash-verge` and `spotify` default to false for new users?
- Removing the `nvim` set while the `nvim` appearance recipe is on: this is allowed, and the card then shows "Needs
  packages".

## 8. ADR
- **Decision:**
  - Add a packages layer with live detection: `mokka/packages.toml` plus `rice/packages.py`, detecting with one
    `pacman -T` and optional cmd/file/unit checks.
  - Recipes declare `needs` as set ids.
  - Installs run only in a visible terminal or TTY, through `rice packages install`, which:
    - install.sh phase 1 uses;
    - `apply --install-needs` uses (CLI only);
    - the GUI uses, with any terminal (U1) and a single result file with a liveness record.
  - The GUI becomes two `Adw.ViewStack` tabs of FlowBox cards.
- **Drivers:**
  - accurate live detection, including `provides`;
  - one package list;
  - a clear GUI with no hidden sudo that works with any terminal.
- **Alternatives considered:**
  - packages inside recipes (B): rejected, because detection would stay on the state file;
  - a bash-sourced list (C): rejected, because it has no metadata and needs a parser;
  - the terminal exit code (D0) or a `sh -c 'echo $? > F'` wrapper (D1): rejected because:
    - handoff and server terminals return immediately, and `wezterm start` always returns 0;
    - a closed window writes nothing, so cancelled and running look the same.
  - streaming output and regex-scanning it for stale-DB errors: rejected, because it breaks the TTY and re-detection
    already gives the answer.
- **Why chosen:** it fixes the root cause (detection) with one small module. It works with any terminal (U1), removes
  the duplicated lists, and keeps the engine semantics frozen.
- **Consequences:**
  - install.sh bootstraps python before phase 1, its blocks are reordered, and the `--only` steps become functions
    dispatched at the top;
  - five recipes are deleted;
  - `apply(packages=)` becomes `install_needs`, and `--no-packages` is a hidden no-op for one release;
  - `Status` loses `missing_packages` and gains `missing_needs` and `run_pending`;
  - stale state is tolerated (U2).
- **Follow-ups:**
  - revisit the app-set defaults after the M-check;
  - `rice packages status --json`, only if a script ever needs it.

## 9. Changelog v2 (review item → where addressed)

| Item | Where |
|---|---|
| A1: one `pacman -T`; `provides`; `any_of` only for firedragon and clash-verge-rev (added); install skips satisfied names; Handy is `missing` | Principle 1, §2.1 table, §2.2, §2.4.6, §3 `unsatisfied`, T1 tests, §6.2, §7 row 1 |
| A2: no `--install-needs` in install.sh; declined needs are skipped with exit 0 | §3 CLI, T3 step 4.6, ADR |
| A5: queued refresh; the worker never assigns `self.status` | §4.5, T4 tests and acceptance grep |
| A6: `--only` dispatch at the top as `step_*` functions with no `rice`; no double run | T3 steps 1–2, T3 tests, §7 row 2, ADR consequences |
| A7: `sys.executable` and `bash -c` | §2.4.2, §3 `terminal_argv`, T1 tests |
| A8: closing message points to `rice packages install` and the GUI Packages tab | T3 step 5 |
| A9: T2 and T3 merged into one atomic task; no `packages install` of step sets before install.sh lands | §5 (new T2, ordering notes) |
| `wezterm start` always returns 0 | §1 option D0, §2.4.5, ADR |
| C1: TTY inherited, no streaming, no `stale_db`/`done` fields; four poll states. The two marker files are superseded by A10's single file | §2.4.3–5, §3 `InstallResult` and `poll_state`, §1 options D2, T1 tests |
| C2: terminal resolution order (U1) and the argv; tests with fake binaries and fake result files | §2.4.1–2, T1 tests, §6.5.4 |
| C3: API frozen before T1 (`unsatisfied`, `InstallResult{rc, failed, message}`); fake pacman implements `-T` | §3 header, T1 tests |
| C4: options table and ADR rejection reasons rewritten around U1 | §1, §8 |
| C5: U2 compatibility test (stale state, `disable` of an unknown id, `packages install` of a stale id → 2) | §2.3, §3 `disable`, T2 U2 test, §7 row 5 |
| C6: old flag mapping, `--nvim` acting on both, `--only pkgs` error, one test per flag | T3 steps 1 and 3, T3 tests |
| C7: `db.lck` hint, never removed; §6.5 is "status matches re-detection" | §2.4.4, Principle 3, §6.5, §7 row 4 |
| C8: streamed-prompt risk row deleted; remove runs without `--noconfirm` | §7, §2.4.7, T1 tests |
| A10: one result file `F` with a `running{pid}` record replaced by `done{rc, failed, message}`; poll every 500 ms; no-start after 30 s with the argv in the Log; never `proc.poll()` or the terminal rc; terminal table with wezterm `--always-new-process --cwd`, foot not footclient, konsole `--separate`, gnome-terminal `--wait` via `$TERMINAL`/xdg, `$TERMINAL` basename mapping; "No terminal found" toast; `cd REPO` and an absolute `sys.executable` inside `bash -c` | §0 row 4, §1 D0/D2, §2.4.1–3 and §2.4.5, §3 `poll_state`, T1 tests, §7 row 3 |
| A11: no pruning of old state entries (owned paths kept); the fixture with yakuake and firedragon; `status()` has no yakuake key; `--no-packages` as a hidden no-op for one release; steps skipped for sets already installed | §2.3, §2.4.6, §3 CLI, T2 U2 test, T1 tests, §7 rows 5–6, ADR consequences |
