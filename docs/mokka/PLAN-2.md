# PLAN-2: Packages layer, appearance `needs`, GUI rework

Status: pending approval

Implements `docs/mokka/SPEC-2.md`. Builds on PLAN.md §2/§3/§12. Engine semantics (ownership, `run_pending`, `noctalia`
op, custom dir) are unchanged. Mode: ralplan SHORT.

## 0. SPEC-2 §5 open questions: resolved defaults

| # | Question | Decision |
|---|----------|----------|
| 1 | Manifest format | One file, `mokka/packages.toml`, with `[[set]]` tables. |
| 2 | Base sets in the GUI | Shown under a collapsed **Base system** expander. Their only action is **Install missing**, never Remove. |
| 3 | `needs` references | Package-set ids only. Theme packages get small sets (`qt-theme`, `cursors`, `fonts`, `gtk-theme`, `sassc`). |
| 4 | Terminal run and result | The GUI spawns `wezterm start --always-new-process -- … python -m rice packages install --result F <ids>`. rice itself writes JSON to `F`, so no shell wrapper is needed. The GUI polls the wezterm `Popen` (500 ms). On exit it reads `F`; a missing file means "cancelled". It then re-reads live status. |
| — | yakuake | Dropped: the recipe and its README row are deleted. |
| — | Recipes that held only packages or `install.sh --only` runs (`base-packages`, `greeter`, `handy`, `firedragon`) | Deleted. They become package sets. Their `mokka/previews/<id>/` dirs are renamed to the set id. |

Not created: `.omc/plans/open-questions.md`, because the caller limited output to this file. The residual questions are in §7.

## 1. RALPLAN-DR summary

**Principles**
1. The live system is the truth for packages. Package state comes from `pacman -Qq`, `systemctl` and files, never from `state.json`.
2. There is one manifest. install.sh, the CLI and the GUI all read `mokka/packages.toml`.
3. Sudo runs only in a visible terminal or in the user's own TTY.
4. Minimal code: stdlib + PyGObject, one new module, no plugin or registry layers.
5. The rice engine (PLAN §2/§12) is untouched apart from the `packages` → `needs` swap.

**Top-3 decision drivers**
1. Detection accuracy. This is the user's main complaint (greeter/Handy/FireDragon show as "off" or "missing").
2. A single source for package lists, with no duplication between install.sh and the recipes.
3. GUI clarity: two tabs, state per card, explicit install actions.

**Options**

| Option | Pros | Cons |
|---|---|---|
| **A (chosen):** a new `rice/packages.py` plus `mokka/packages.toml`. Recipes declare `needs = [set ids]`. install.sh calls `rice packages …`. | The layers are cleanly split. Detection is live and fast (one `pacman -Qq`). T1 can be built in parallel because it is a new file. | install.sh now depends on python early, so it bootstraps python. User-file steps in install.sh must be reordered. |
| B: keep packages in recipes, as "apps" components with only `packages`. | Least new code. | It keeps mixing the layers. Status stays tied to `state.json`, so the root bug remains. Recipes cannot express any_of or detection. |
| C: lists in a bash-sourceable `mokka/packages.sh` that rice parses. | install.sh needs no python. | rice would parse bash. There are no titles or detection rules. GUI metadata would live somewhere else. |
| D1: a terminal wrapper `sh -c 'cmd; echo $? > F'`. | Trivial. | Only an exit code, with no stale-DB or failed-set detail. |
| **D2 (chosen):** rice writes the JSON result, and the GUI watches the process exit. | Rich result. Closing the window reads as cancelled. | About 10 more lines. |

**Invalidation:**
- B fails driver 1 because detection would still depend on state.
- C fails principle 4 (a bash parser in python) and driver 3 (no metadata).
- D1 cannot report SPEC §4 "stale DB → run cachy-update", so it fails the requirements.

## 2. Data model

### 2.1 `mokka/packages.toml`
```toml
[[set]]
id = "firedragon"                 # required, unique, [a-z0-9-]+
title = "FireDragon browser"      # required
description = "Replaces Firefox; Mod+B opens FireDragon."
group = "apps"                    # required: base | theme | apps | dev
default = true                    # install.sh preselects it (base sets are always installed)
repo = []                         # pacman packages
aur = ["firedragon-catppuccin-bin"]
any_of = [["firedragon-catppuccin-bin", "firedragon"]]  # equivalence groups for detection
step = "firedragon"               # optional: run `bash install.sh --only <step>` after the packages
installed_if = { cmd = "firedragon" }  # optional extra checks, all must hold: cmd | file | unit
removable = true                  # optional, default false; only for group apps|dev, and only without a step
```
- `any_of`: a listed package counts as present if it, or any member of an `any_of` group containing it, is in `pacman -Qq`.
  Install always installs the listed name.
- `installed_if` keys:
  - `cmd`: `shutil.which`.
  - `file`: a path, `~`-expanded.
  - `unit`: `systemctl is-enabled <unit>` returns 0.
- There is no `post` or `remove` script field. FireDragon's "remove firefox and rebind Mod+B" moves into
  `install.sh --only firedragon`. Podman's subuid/subgid lines move into `--only podman`.
- Validation in `packages.load()` raises `ValueError("<file>: set[<i>].<field>: …")` for:
  - a missing required field;
  - an unknown group;
  - a duplicate id;
  - `removable` on a base set or on a set with a `step`;
  - an unknown `installed_if` key.

**Migration table.** The executor copies these exactly. Every former `REPO_PKGS`/`AUR_PKGS` and recipe `packages` entry
lands in exactly one set.

| id | group | default | repo | aur | step / installed_if |
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
| firedragon | apps | true | | firedragon-catppuccin-bin | step firedragon; any_of with firedragon |
| nvim | apps | true | neovim git ripgrep fd lazygit tree-sitter-cli unzip | | removable |
| micro | apps | true | micro | | removable |
| telegram | apps | true | telegram-desktop | tg-ws-proxy-bin | removable |
| filezilla | apps | true | filezilla | | removable |
| clash-verge | apps | true | | clash-verge-rev-bin | removable |
| spotify | apps | true | | spotify-launcher | removable |
| dev | dev | true | nodejs npm openssh k9s podman podman-compose slirp4netns fuse-overlayfs | | step podman |

`git` appears in both `core` and `nvim`. That is allowed: `--needed` makes it harmless, and removing `nvim` drops only
the packages that no other installed set lists. Bootstrap literals stay in install.sh as a documented exception:
`python`, `cachy-update` and `libnotify`, plus the paru build.

### 2.2 Detection rules
- `packages.installed_names()` runs `pacman -Qq` **once**. It returns a `set[str]`, or `None` when pacman is absent. With
  `None`, package checks pass, which keeps today's behaviour on non-Arch hosts and in tests.
- `missing(set)` returns the listed packages not satisfied (taking `any_of` into account), plus `"unit:<u>"`, `"cmd:<c>"`
  or `"file:<f>"` for failed `installed_if` checks.
- The state of a set:
  - `installed` when `missing == []`;
  - `partial` when some, but not all, listed packages are present;
  - `missing` otherwise.

### 2.3 Recipes declare `needs`
- `needs = ["qt-theme"]` at the top level, plus `variants.<n>.needs`. The effective needs are top-level ∪ the chosen variant's.
- `recipe.load()` **rejects** a `packages` key with `packages: moved to mokka/packages.toml, use needs = [set ids]`.
  `Component.packages` and `Variant.packages` are replaced by `needs: list[str]`.
- Recipe mapping:
  - qt → `qt-theme`
  - cursor → `cursors`
  - fonts → `fonts`
  - gtk: variant noctalia → `gtk-theme`, variant colloid → `sassc`
  - fish, bat, starship, fastfetch → `shell`
  - micro → `micro`
  - nvim → `nvim`
- `only_if_installed` (konsole-kate) stays. It describes an app the user may or may not have, not a need.
- `APPS_RUN` is deleted from cli.py, because its components no longer exist.
- Stale `state.json` entries for the deleted ids are ignored, since status iterates recipes. Their owned paths stay
  owned, which is harmless.

### 2.4 Terminal-run protocol
1. The GUI creates `F = $XDG_RUNTIME_DIR/rice-pkg-<uuid>.json`, or uses `tempfile` as a fallback, and launches
   `packages.terminal_argv(action, ids, F)`, which is:
   `["wezterm","start","--always-new-process","--cwd",REPO,"--","sh","-c",
     'python -m rice packages <action> --result "$0" "$@"; echo; read -r -p "Press Enter to close" _', F, *ids]`.
2. rice runs pacman, the helper and the step inline in that TTY, so sudo prompts there. In a `finally` block it writes
   `F` atomically (tmp + `os.replace`) with
   `{"rc": int, "done": [set ids], "failed": [set ids], "stale_db": bool, "message": str}`.
3. The GUI polls `proc.poll()` every 500 ms with `GLib.timeout_add`. On exit it calls `packages.read_result(F)`:
   - a result shows a toast (installed / failed / stale DB);
   - `None` shows "cancelled".
   It then deletes `F`, refreshes (§4.5) and re-enables the buttons.
4. **Stale DB.** pacman and helper output is streamed line by line to stdout (`Popen(stdout=PIPE, stderr=STDOUT)`, echoed)
   and scanned for `failed retrieving file` or `404`. On a hit the install stops: `stale_db=true`, and the message reads
   "Package database is stale: run cachy-update (or sudo pacman -Syu) first, then retry." There is never a `-Sy`/`-Syu`.
5. Install order per call: the missing repo packages of all sets in one `sudo pacman -S --needed --noconfirm`. If that
   fails without a stale DB, each package is retried alone. Failed repo packages plus AUR packages then go to
   `paru|yay -S --needed --noconfirm`. Then each set's `step` runs as `bash {REPO}/install.sh --only <step>`, in manifest
   order. A set whose packages or step failed goes to `failed`.
6. Remove is `sudo pacman -Rns <packages of the set that are installed and not listed by another installed set>`, **without**
   `--noconfirm`, so pacman's own prompt is the second confirmation. It is allowed only when `removable` is set.

## 3. Frozen API contract (T1/T3 implement it; T4/T5 code against it from day one)

```python
# rice/packages.py  (new)
MANIFEST = REPO / "mokka/packages.toml"
GROUPS = ("base", "theme", "apps", "dev")
@dataclass class PkgSet: id; title; description; group; default: bool; repo: list[str]; aur: list[str]
    any_of: list[list[str]]; step: str|None; installed_if: dict; removable: bool
@dataclass class SetStatus: state: str  # installed|partial|missing
    missing: list[str]                  # package names and "unit:/cmd:/file:" tokens
@dataclass class InstallResult: rc: int; done: list[str]; failed: list[str]; stale_db: bool; message: str
load(path=MANIFEST) -> list[PkgSet]                     # validated, manifest order
installed_names() -> set[str] | None                    # one `pacman -Qq`
status(sets=None, names=...) -> dict[str, SetStatus]    # names defaults to installed_names()
install(ids, log=print) -> InstallResult                # inline, already-installed sets are no-ops
remove(ids, log=print) -> InstallResult                 # removable sets only, else rc=1 with a message
terminal_argv(action: str, ids: list[str], result: Path) -> list[str]   # action in {"install","remove"}
write_result(path, r: InstallResult) -> None; read_result(path) -> InstallResult | None

# rice/recipe.py (changed)
Variant: name; title; needs: list[str]; steps            # `packages` removed
Component: … needs: list[str] …                          # `packages` removed, rest unchanged
needs(c, variant=None) -> list[str]                       # top-level ∪ variant needs, ordered, unique

# rice/state.py Status (changed)
missing_packages  -> removed
missing_needs: list[str]   # set ids of effective needs that are not `installed`
run_pending: bool          # from state entry; the GUI derives "Waiting for Noctalia" from it

# rice/cli.py (changed)
apply(ids, variants=None, dry_run=False, force=False, install_needs=False, run=True, log=print) -> dict[str, Result]
    # install_needs: before processing, packages.install(<missing needs of selected comps>) once, inline.
    # A comp whose effective needs are still missing -> Result("skipped", message="needs a, b"); nothing written.
    # Dry run: same skip; never installs.
status(remote=False) -> dict[str, Status]   # one packages.status() call per status()
```
`apply(packages=…)` is renamed to `install_needs`. The two test call sites (`test_noctalia.py:109`, `test_cli.py:184`)
are updated mechanically.

**CLI**
- `rice packages list` prints one TSV line per set, `id<TAB>group<TAB>default(0|1)<TAB>title`, in manifest order.
  install.sh consumes it.
- `rice packages status` prints `id: installed|not installed|partial (missing: a b)`.
- `rice packages install <ids…> [--result F]` exits with `InstallResult.rc` and prints `message`.
- `rice packages remove <ids…> [--result F]` works the same way.
- An unknown set id exits 2 with `unknown set(s): …`.
- `rice apply … --install-needs` is new. `--no-packages` is removed.
- The `apply` exit code is 1 if any result is `failed`, **or** any id named explicitly (positional or `--with`) was
  skipped with `needs …`. Components pulled in by `--defaults` that are skipped for needs exit 0.
- In `rice status`, the flag `pkg:<name>` is replaced by `needs:<set id>`. The new flag `run-pending` is added.

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
    │   page "appearance" (title "Appearance", icon applications-graphics-symbolic) — default
    │   page "packages"   (title "Packages",   icon package-x-generic-symbolic)
    │   each page: Gtk.ScrolledWindow > Adw.Clamp(max 1500) > Box(V, spacing 24, margin 24) > sections
    │   section: Box(H, spacing 8){Gtk.Image(group icon), Label(group title, .title-2)}
    │            + Gtk.FlowBox(homogeneous, min 1 / max 3 per line, selection NONE, spacing 18)
    │   Packages "base" section: Gtk.Expander(label "Base system (always installed)", expanded=False) > FlowBox
    └ bottom: Adw.ViewSwitcherBar(stack, reveal when narrow) + Gtk.Expander "Log" > ScrolledWindow(TextView, 140px)
              (the log expander opens automatically on an error)
```
Group titles and icons are one dict in gui.py:
- Appearance:
  - look: "Look & palette", `preferences-desktop-appearance-symbolic`
  - qt: "Qt & KDE apps", `applications-system-symbolic`
  - gtk: "GTK", `applications-graphics-symbolic`
  - shell: "Shell", `utilities-terminal-symbolic`
  - terminal: "Terminal apps", `utilities-terminal-symbolic`
  - apps: "App configs", `applications-other-symbolic`
  - wallpapers: "Wallpapers", `preferences-desktop-wallpaper-symbolic`
- Packages:
  - base: "Base system", `computer-symbolic`
  - theme: "Theme packages", `preferences-desktop-theme-symbolic`
  - apps: "Applications", `applications-other-symbolic`
  - dev: "Development", `applications-engineering-symbolic`

### 4.2 Appearance card (Box V, `.card`, width_request 360)
1. Preview, height 220: an `Adw.Carousel` of `Gtk.Picture` (content_fit COVER) with `Adw.CarouselIndicatorDots` when
   there are more than one. A `Gtk.GestureClick` opens the fullscreen preview at the current index. With no pictures
   it shows the placeholder and the label "Fetch to see preview".
2. Palette only: a swatch **row** under the preview (`Box` homogeneous, height 28, one box per color, no gaps). It is
   never a carousel page, which fixes the overlap.
3. Body (margin 12, spacing 6):
   - title `.heading`;
   - description `.dim-label`, wrap, 2 lines, ellipsized;
   - state line `Label` with a CSS class from §4.4;
   - `Gtk.DropDown` "Variant" when there are variants;
   - when needs are missing, `Label "Needs: <missing package names>"` plus `Button "Install packages"` and a caption
     "Opens a terminal, needs sudo";
   - a footer row with an "Enabled" `Gtk.Switch` at the end. A non-reversible component shows
     the caption "Turning off keeps current settings".
   - The switch is insensitive while needs are missing and the component is off.

### 4.3 Package card (same frame, width_request 360)
- A preview (height 140) when `mokka/previews/<set id>/*.png` exists.
- The title, the description, and a package line `.dim-label .caption`: "repo: a b · AUR: c".
- The state line:
  - **Installed ✓** `.success`;
  - **Not installed** `.dim-label`;
  - **Partially installed: missing a, b** `.warning`.
- Buttons:
  - **Install** `.suggested-action` when the state is not installed. On base sets the label is "Install missing".
  - **Remove** `.destructive-action` only when `removable` and installed, behind `Adw.AlertDialog` ("Remove <title>?
    This runs pacman -Rns in a terminal."). The dialog's buttons are Cancel and Remove.
  - The caption under the buttons reads "Opens a terminal, needs sudo".
  - All Install and Remove buttons are insensitive while a terminal run is active (one run at a time).

### 4.4 State labels (pure `appearance_state(c, s, pending) -> (text, css)`; the first match wins)
1. `pending == "on"` → "Will be applied" `.accent`. `pending == "off"` → "Will be turned off" `.accent`.
2. `s.app_missing` → "App not installed" `.dim-label`.
3. `s.missing_needs` → "Needs packages" `.warning`.
4. not `s.enabled` → "Off" `.dim-label`.
5. `s.modified` → "Hand-edited" `.warning`.
6. `s.run_pending` and the component has a `noctalia` step → "Waiting for Noctalia" `.warning`.
7. `s.needs_apply is None` → "Not built yet" `.dim-label`.
8. `s.needs_apply` or `s.recipe_changed` or `s.stale_sources` → "Needs apply" `.accent`.
9. Otherwise → "Applied ✓" `.success`.

`pending` comes from `selection()` (switch/variant differs from status, or enabled and needs apply). Components with
missing needs are excluded from `on`. `N = len(on) + len(off)`. The header summary is the pure
`summary(n, updates: bool|None)`.

### 4.5 Refresh
- `self.cards[id]` keeps the widget handles: state label, needs label/button, switch, dropdown, install/remove buttons.
- `refresh()` runs `cli.status()` and `packages.status()` in `run_bg`. On completion `sync()` updates labels, buttons
  and the header **in place**, so the user's pending toggles survive.
- `refresh()` runs after Apply, after a terminal run exits (§2.4), and after Check for updates (which uses
  `status(remote=True)`).

### 4.6 Fullscreen preview
- A `Gtk.Window(transient_for=win, modal=True)` with a `.preview-bg` black CSS class, `fullscreen()`, and an
  `Adw.Carousel` of `Gtk.Picture` (CONTAIN) starting at the clicked index.
- Escape (`Gtk.EventControllerKey`) or a click closes it. Left/Right arrows page through.

### 4.7 Kept actions
Check for updates, Fetch, Show changes (the existing `show_text` dialog), Open custom folder, and Apply (apply on, then
disable off, then refresh). Everything else in the current gui.py (sidebar, `PreferencesPage`s, the `badges()` string)
is deleted.

`--smoke`:
- visits both stack pages;
- asserts at least one paintable picture and at least one card on each page;
- opens and closes the fullscreen preview for the first card that has pictures;
- exits 0.

## 5. Task breakdown

```
T1 ─┬─> T3 ─┬─> T4 ─┐
T2 ─┘       └─> T5 ─┴─> T6
```
T1 ∥ T2 (disjoint files). T4 ∥ T5 (disjoint files). `rice/cli.py` is touched **only by T3**. README is touched **only by T6**.

### T1: Packages layer (new files only)
- **Files:** `rice/packages.py`, `mokka/packages.toml`, `tests/test_packages.py`.
- **Steps:**
  1. Write the manifest from the §2.1 table.
  2. Implement §3 `packages.*` with streaming output and stale-DB detection.
  3. Implement `terminal_argv`, `write_result` and `read_result`.
- **Tests** (hermetic: a temp dir of fake `pacman`, `sudo` (execs `"$@"`), `paru` and `systemctl` scripts prepended to
  PATH, logging argv to a file; HOME in tmp):
  - load errors name the field;
  - status installed/partial/missing, `any_of` (`firedragon` satisfies `firedragon-catppuccin-bin`), and
    `installed_if` cmd/file/unit;
  - `names=None` means satisfied;
  - install sends only missing packages, in one pacman call;
  - a repo failure retries per package, then the helper;
  - the AUR packages go to paru;
  - fake pacman printing `error: failed retrieving file 'x' from … : 404` gives `stale_db=True`, rc≠0, and no
    helper call;
  - `step` runs `install.sh --only <step>`; this is checked by monkeypatching `packages.INSTALL_SH` to a fake script;
  - remove refuses a non-removable set and keeps a package shared with another installed set;
  - the result roundtrip, and a missing file gives `None`;
  - `terminal_argv` contains `--always-new-process` and the result path.
- **Accept:** `python -m unittest tests.test_packages -v` is green. `python -c "from rice import packages; print(len(packages.load()))"` prints `18`.

### T2: Recipe migration (parallel with T1)
- **Files:**
  - `rice/recipe.py`;
  - `mokka/components/{qt,cursor,fonts,gtk,fish,bat,starship,fastfetch,micro,nvim}.toml`;
  - delete `mokka/components/{base-packages,greeter,handy,firedragon,yakuake}.toml`;
  - `git mv mokka/previews/base-packages mokka/previews/core`;
  - `tests/test_recipe.py`, `tests/test_recipes_data.py`.
- **Steps:**
  1. Swap `packages` → `needs` in the dataclasses and `load()`. `load()` rejects `packages`. Add `recipe.needs()`.
  2. Edit the recipes per §2.3.
  3. Fix the `test_recipe.py` Component literal (line ~161).
- **Data tests:**
  - no recipe has a `packages` key;
  - every `needs` id exists in `packages.load()`; the import is local inside the test, so it runs once T1 is merged;
  - every package from the frozen former lists (inline in the test) appears in some set.
- **Accept:** `python -m unittest tests.test_recipe tests.test_recipes_data tests.test_previews -v` is green once T1 is
  merged. `grep -L '^packages\|\[packages\]' mokka/components/*.toml | wc -l` equals the component count.

### T3: CLI integration (after T1 and T2; sole owner of `rice/cli.py`)
- **Files:** `rice/cli.py`, `rice/state.py` (the Status fields), `tests/test_cli.py`, `tests/test_noctalia.py`
  (the kwarg rename only).
- **Steps:**
  1. Delete `_pkgs`, `_have_pkg`, `_missing_packages`, `_install_packages` and `APPS_RUN`.
  2. Add `_unmet(c, variant, pst)`, which uses one `packages.status()` per `_apply`/`status`.
  3. Add the `install_needs` flow and the skip with `needs …`.
  4. Replace the `packages` branch of the theme-dir check with `not dry`.
  5. Add `Status.missing_needs` and `Status.run_pending`.
  6. Add the `packages` subparser (`list|status|install|remove`, `--result`).
  7. Implement the `--install-needs` flag, the exit-code rule, and the status flags.
- **Tests:** patch `packages.MANIFEST` to a tmp manifest and `packages.installed_names` to a fixed set.
  - an unmet need → `skipped` "needs x", with no state entry and no files;
  - `main(["apply","alpha"])` → 1 when alpha is skipped for needs;
  - `--defaults` → 0;
  - `install_needs=True` calls the (mocked) `packages.install` once with only the missing ids, then applies;
  - a dry run never calls install;
  - `status()["alpha"].missing_needs`;
  - `main(["packages","list"])` prints TSV;
  - `main(["packages","install","nope"])` → 2.
- **Accept:** `python -m unittest discover -s tests -v` is fully green. `python -m rice packages status` runs on this
  machine and shows greeter/handy/firedragon as `installed` (manual, read-only).

### T4: install.sh two phases (after T3; parallel with T5)
- **Files:** `install.sh`, `tests/test_install_sh.py` (new, static checks plus safe invocations).
- **Steps:**
  1. Delete `REPO_PKGS`/`AUR_PKGS`.
  2. Flag parsing:
     - generic `--no-<id>`/`--<id>` into `OPT[id]`, validated against `rice packages list` after bootstrap;
     - keep `--no-pkgs`, `--no-wallpapers`, `--no-mokka`, `--no-agents`, `--gui`, `--fish-theme`, `--yes` and `update`;
     - `--only` accepts `greeter,handy,firedragon,podman`;
     - `pkgs` is removed from `--only`.
  3. Bootstrap (when pkgs is on):
     - `sudo pacman -S --needed --noconfirm python cachy-update libnotify`;
     - interactive cachy-update and the paru bootstrap stay unchanged;
     - with `--no-pkgs`, require `command -v python` or exit with a message.
  4. Reorder:
     1. user files (`install_tree`, seds, custom seed);
     2. **phase 1**: read `python -m rice packages list`. Base sets are always chosen. For the others,
        `ask <id> "Install <title>?" <default>`. Then `python -m rice packages install "${chosen[@]}"`;
     3. post-package user setup (gsettings, tg-ws-proxy autostart, cachy-update tray, npm agents, initrd, fish);
     4. the Handy keybind drop when handy is not chosen or installed;
     5. **phase 2**: `python -m rice apply --defaults --yes --install-needs` (`--install-needs` only when pkgs is on),
        with `--with`/`--without` for nvim/agents. FireDragon is no longer a component.
  5. Move the FireDragon firefox-removal/keybind block into `--only firedragon`, and the subuid/subgid lines into
     `--only podman`.
  6. `--only` still exits before any rice call.
- **Tests:**
  - `bash -n install.sh`;
  - `shellcheck install.sh`, skipped if absent;
  - no `REPO_PKGS|AUR_PKGS` and no `pacman -S` literal except the bootstrap line;
  - `bash install.sh --only bogus` exits 1 with "unknown --only step";
  - the set of manifest `step` values equals install.sh's `--only` case pattern, parsed by regex (this check lives here,
    not in T2, because the new steps only land in T4);
  - in the `--only` code path, `rice` does not appear before the `[[ -z $ONLY ]] || exit 0` guard (regex on the
    file order).
- **Accept:** `python -m unittest tests.test_install_sh -v` is green. A manual dry read on this machine:
  `bash install.sh --only greeter` (needs sudo, done by the user in M-check) still works.

### T5: GUI rework (after T3; parallel with T4)
- **Files:** `rice/gui.py`, `tests/test_gui_smoke.py`, `tests/test_gui_logic.py` (new).
- **Steps:** build §4 exactly. The pure helpers `appearance_state`, `summary` and `pending(selection, status)` live at
  module top level in gui.py.
- **Tests:**
  - `test_gui_logic` imports `rice.gui` and skips if `gi` is missing (Gtk4 import needs no display). It checks every
    `appearance_state` branch and its order, and the `summary` strings;
  - the smoke test is extended per §4.7.
- **Accept:** `python -m unittest tests.test_gui_logic -v` is green. In a Wayland session,
  `python -m rice gui --smoke; echo $?` prints `0`.

### T6: Docs and verification (after T4 and T5)
- **Files:** `README.md` (install flags, the two phases, the Packages/Appearance tabs, `rice packages`, the yakuake row
  removed). The PLAN-2 changelog is appended by the main thread.
- **Steps:** run §6 and record the results.
- **Accept:** all of §6 passes.

## 6. Verification plan
1. **Unit (hermetic):** `python -m unittest discover -s tests -v` is green. No test calls real `pacman`, `sudo`,
   `systemctl`, `noctalia` or `wezterm`, and none writes outside tmp. Grep check: `grep -rn '"sudo"\|"pacman"' tests/`
   only finds fake-script creation.
2. **CLI on this machine (read-only):**
   - `python -m rice packages status`: greeter, handy, firedragon and core are `installed`. Nothing is falsely
     missing. This is the SPEC §1 bug.
   - `python -m rice status` shows no `needs:` flag for installed sets.
   - `python -m rice apply --defaults --dry-run --no-run` prints `0 changes`, or only expected changes.
3. **GUI smoke:** `python -m rice gui --smoke` exits 0, through `tests/test_gui_smoke.py` under `WAYLAND_DISPLAY`.
4. **Screenshot manual check (M-GUI):**
   1. Launch `python -m rice gui`.
   2. Capture with `niri msg action screenshot-window` at full width (about 1900 px) and after resizing to about 900 px.
   3. Check:
      - 3 columns, then 2 or 1;
      - no horizontal scroll;
      - both tabs present;
      - group titles are human-readable with icons;
      - the palette swatch row sits under the preview without overlap;
      - every card has a state line;
      - the header shows "N pending · upstream updates: …" and "Apply (N)";
      - Install buttons carry "Opens a terminal, needs sudo";
      - the Base expander is collapsed;
      - clicking a preview goes fullscreen and Esc closes it.
   4. Save the shots under `docs/mokka/m-checks/`. Optionally run `oh-my-claudecode:visual-verdict` against this
      checklist.
5. **Terminal flow (manual):**
   1. Uninstall `micro`, or use any small removable set, by hand.
   2. Its Appearance card shows "Needs packages".
   3. Click Install: wezterm opens, sudo prompts, it closes on Enter.
   4. The toast reads "Installed: micro". The card flips to "Off"/"Will be applied" without a restart.
   5. Close a terminal mid-run: the toast reads "cancelled", and the status is unchanged.
6. **install.sh:** `bash -n`, shellcheck, `--only bogus`. The full run in a fresh VM or container is the user's
   M-check, not CI.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `pacman -Qq` does not list `provides`, so renamed or alternative packages show as missing. | `any_of` in the manifest. The live-system M-check (§6.2) catches gaps before release. |
| The install.sh reorder breaks a fresh-install dependency, e.g. the Handy autostart append before `install_tree`. | The new order puts files first, then packages and their steps, then user setup. The T4 test asserts the textual block order. A full VM run is the user's M-check. |
| Streaming pacman through a pipe degrades progress bars, or hides the sudo prompt. | sudo prompts on `/dev/tty`, not stdout. Degraded progress bars are cosmetic and accepted. |
| wezterm is missing, or `--always-new-process` is ignored, so the process exits early and the result looks like "cancelled". | wezterm is in `core`. If `shutil.which("wezterm")` is None the GUI shows an error toast. The protocol only trusts the result file. |
| Stale state entries (yakuake whole files) stay owned. | They are harmless: no `home/` copies exist for them. Documented, no cleanup code. |
| `micro` and the other app sets default to true, so the install.sh prompts grow. | Defaults come from the manifest, and `--yes` keeps the behaviour non-interactive. |

**Residual questions** (defaults applied, revisit after M-check):
- Should `telegram`, `clash-verge` and `spotify` default to false for new users?
- Should `nvim`, the app set, be removable when the `nvim` appearance recipe is enabled? The default is yes. The
  appearance card then shows "Needs packages".

## 8. ADR
- **Decision:** add a live-detected packages layer (`mokka/packages.toml` plus `rice/packages.py`). Recipes declare
  `needs` on set ids. Installs happen only in a visible terminal or TTY, through `rice packages install`. That command
  is used by install.sh phase 1, by `apply --install-needs`, and by the GUI. The GUI is rebuilt as two `Adw.ViewStack`
  tabs of FlowBox cards.
- **Drivers:** accurate detection from the live system; one package list; a clear GUI with no hidden sudo.
- **Alternatives considered:**
  - packages inside recipes (B), rejected because state-file detection keeps the bug;
  - a bash-sourced list (C), rejected for having no metadata and needing a parser in python;
  - an exit-code wrapper (D1), rejected because it cannot report stale DBs or failed sets.
- **Why chosen:** it fixes the root cause (detection) with one small module. It removes duplication, and it keeps the
  engine semantics frozen.
- **Consequences:**
  - install.sh needs python before phase 1 (bootstrap), and its blocks are reordered;
  - five recipes are deleted;
  - `apply(packages=)` becomes `install_needs`, and `--no-packages` is gone;
  - Status loses `missing_packages` and gains `missing_needs` and `run_pending`.
- **Follow-ups:**
  - revisit the app-set defaults after the M-check;
  - an optional `rice packages status --json` if a script ever needs it (not now).
