# PLAN: Garuda Mokka -> niri-noctalia-rice (implements docs/mokka/SPEC.md v1)

Status: pending approval
Mode: ralplan consensus, SHORT. Planner draft 4 (2026-11-16). Changes are listed in §9 (v2), §10 (v3) and §11 (v4, verifier).
Consensus: Architect SOUND (after R1), Critic APPROVE (iteration 3). Pending: verifier pass, then user execution approval.

Fixed inputs (not re-opened): upstream Garuda repos are the source of truth. They are fetched at install time into
`~/.cache/niri-noctalia-rice/upstream` and patched by declarative rules. Pins come from tags or PKGBUILD pins, else
from Garuda's branch. On update we show a diff and re-apply only what changed. The GUI is Python + PyGObject + GTK4 +
libadwaita with previews. Noctalia drives colors (variant A). Stdlib only (Python 3.14 has `tomllib`), no pip deps,
tests via `python -m unittest`.

## 0. Facts from the real upstream that change the SPEC

Checked against pkgbuilds `3d68043`, garuda-mokka tag `2.0.9` (`7e0a1c7`), and this machine:

1. `mokka-fastfetch.png` ships in garuda-mokka (`usr/share/icons/garuda/`). `garuda-icons` is **not needed in v1**.
2. The `sha256sums` of `git+https://...#commit=X` sources are makepkg VCS checksums and cannot verify a GitHub
   tarball. GitHub sources are therefore fetched with `git fetch --depth 1 origin <commit>`, and the commit hash
   verifies the content, so no sha is stored. sha256 is checked only for same-URL sources: the GitLab `-/archive/<commit>`
   tarballs (garuda-mokka, garuda-wallpapers), `done.fish`, and the pkgbuilds-local files (`config.fish`,
   `starship.toml`, fetched as GitLab raw at the pinned commit). `lock` makes no network calls except to fetch PKGBUILDs.
3. `catppuccin/kde`, `conky`, and `grub` are only consumed by Plasma-only PKGBUILD build steps. They are recorded in
   the lock, but nothing fetches them.
4. Noctalia owns colors in shared files. Template ids are as defined in Noctalia 5.3.0
   `/usr/share/noctalia/assets/templates/builtin.toml`:
   - `kcolorscheme` writes `$XDG_DATA_HOME/color-schemes/noctalia.colors` and applies it to `kdeglobals`
     (`post_action kde-color-scheme`).
   - `qt` writes `qt5ct/colors/noctalia.conf` and `qt6ct/colors/noctalia.conf`.
   - `gtk3` and `gtk4` write `gtk-{3,4}.0/noctalia.css`, and their `gtk/apply.sh` post_hook resets gsettings
     `gtk-theme`.
   - `starship` writes `$XDG_CACHE_HOME/noctalia/starship-palette.toml`, and its post_hook injects a block into
     `starship.toml`.

   **Confirmed on this machine:** the repo and live `config.toml` list the legacy ids `"gtk"` and `"kde"`, which no
   longer exist and so do nothing. The live `noctalia.css` and `qt6ct/colors/noctalia.conf` are stale (Oct 21), and
   `~/.local/share/color-schemes/noctalia.colors` is absent. The `palette` component fixes the ids (T6). The Noctalia UI rewrites `config.toml`. Rice therefore never writes color keys into these files
   and never hash-tracks them (§2).
5. `rules.kdl` has a catch-all corner-radius rule *before* the steam/gamescope override. Any rule added in a later
   include would override the games rule. So no window-rules go in an include. The radius is a `replace` in
   `rules.kdl`, and `QT_QPA_PLATFORMTHEME` is a `replace` in `misc.kdl`. The rice-owned `cfg/rice.kdl` holds only `cursor {}`.
6. `qt6ct-kde` and `catppuccin-cursors-mocha` are in chaotic-aur on this machine and need AUR elsewhere. The upstream
   Kvantum config uses `theme=Mokka`, and kdeglobals sets `widgetStyle=kvantum-dark` plus Inter Bold fonts.
7. `install.sh:91-94` sets gsettings `gtk-theme`/`cursor-theme`/`font-name`, which conflict with the
   `gtk`/`cursor`/`fonts` components. `install.sh` appends the Handy autostart line unconditionally, so a run that
   skips `install_tree` would add it twice.

## 1. RALPLAN-DR summary

**Principles**
1. One code path. The CLI and GUI call the same frozen API (§3), and diff is `apply(dry_run=True)`.
2. Declarative over imperative. What a component does lives in a TOML recipe, and code holds only 8 fixed ops.
3. Surgical edits to shared files, applied to the live file at apply time. Noctalia owns colors, and rice never
   writes color keys into files that Noctalia templates manage.
4. Idempotent and offline after fetch. A second `apply` writes nothing and creates no `.bak`.
5. Never execute downloaded code. The one exception is Colloid `install.sh`, which is opt-in and labelled.

**Decision drivers (top 3)**
1. Cheap upstream updates: re-pin, re-apply only the affected components, show a diff first.
2. The GUI needs structured per-component metadata: group, previews, variants, packages, reversibility.
3. Minimal, dependency-free, offline-testable code.

**Viable options**

| Option | Pros | Cons |
|---|---|---|
| A. TOML recipes + small op interpreter in Python | Metadata and steps live in one data file, which both the GUI and change detection read. Each op is tested once. Adding a component needs no code. The recipe hash detects changes | 8 ops to implement. The recipe schema is a mini-DSL to document |
| B. One Python module per component | No DSL, full flexibility | Copy/patch logic repeated across ~20 modules. Change detection needs code hashing. GUI metadata is scattered |
| C. Extend bash `install.sh` | No new language | No GTK4 GUI, so the logic ends up in two languages anyway. INI/TOML/JSON edits and diffs are fragile. Unit tests are impractical |

**Invalidation:** C fails drivers 2 and 3. B works but fails driver 1, because "what changed" would need code hashes
instead of a recipe hash, and it duplicates most of the logic. **A chosen.** B's flexibility survives through
`generate`, a named Python function used only by the palette.

## 2. Data model and semantics

**Files:** `rice/{__init__,__main__,lock,fetch,recipe,palette,state,cli,gui}.py`, `tests/test_*.py`,
`tests/fixtures/{pkgbuilds,upstream}/...`, `mokka/upstream.lock.toml`, `mokka/components/*.toml`,
`mokka/files/*` (repo-side files for variants), `mokka/previews/<id>/*.png`, `mokka/capture-previews`.

**Recipe fields:** `id, title, description, group` (look|qt|gtk|shell|terminal|apps|wallpapers), `default`,
`conflicts, requires, packages.repo, packages.aur, only_if_installed, previews, sources, steps, off_steps,
niri_snippet, variants.<name>.{title, packages, steps}, default_variant`.
Source references are written `<lock-id>:<path>`, or `repo:<path>` for files in this repo.

**8 ops.** Each op is one of two kinds:
- *Whole outputs* (`copy`, `generate`, `run when="build"`) are rendered into `~/.cache/niri-noctalia-rice/build/<id>/`
  and cached by key `(source refs, recipe_hash, variant)`. They are tracked in state by sha256. If the live sha
  differs from the recorded sha, the file was edited by hand: it is not overwritten without `--force`, and a diff is
  shown.
- `copy` with `track=false` makes a **seed** file. It is written only when the target is missing or the component's
  variant changed. Its sha is never stored, so later edits by other tools are never reported as hand edits and never
  overwritten. Two users:
  - the starship `noctalia` variant, because Noctalia's starship post_hook injects a marker block into `starship.toml`;
  - `nvim`'s `lazy-lock.json`, because lazy.nvim rewrites it.
- *Patches* (`replace`, `drop_lines`, `ini_set`, `toml_set`, `json_set`) are pure `text -> text` functions. Where a
  patch is applied depends on its target:
  - **Target is also a whole `copy` output** (fish `config.fish`, fastfetch `config.jsonc`, agents `@HOME@`): the
    patch is applied to the staged text **before hashing**, so it is part of the whole file and its sha.
  - **Target is a source-tree path** (the Colloid scratch copy): the patch is applied before `run when=build`.
  - **Target is a file rice does not copy**: the patch is a *live patch* and runs **at apply time against the live
    target** (or against the repo `home/` copy if the target is missing). Components
  are applied one after another, each reading the result of the previous one, so edits to shared files
  (`config.toml`, `kdeglobals`) compose. Live-patch targets get **no sha in state**. Their status is "re-applying is a
  no-op" (clean) or "needs apply".
- `toml_set` value forms: a literal, `{add = "x"}`, or `{remove = "x"}` for arrays (so `palette`, `gtk` and
  `starship` can all edit `builtin_ids`). `ini_set` creates a missing file or section. `toml_set` and `json_set` also create a missing
  file. `ini_set` and `toml_set` are
  line-based: comments, key case, KConfig `[$e]`, and untouched lines
  stay byte-identical.
- `run when="apply"` runs after files are installed (`bat cache --build`, `gsettings set ...`,
  `noctalia msg templates-apply`).
  - **Trigger:** it runs (and is listed in a dry run) only when one of these holds:
    - the component is newly enabled;
    - its variant changed;
    - its `recipe_hash` or refs changed;
    - the apply produced a non-empty file Change;
    - `run_pending` is true in state, meaning an earlier run was skipped by `--no-run` or a command exited
      non-zero.
    A second unchanged apply therefore runs 0 commands.
  - `--no-run` skips all `when=apply` steps (`when=build` steps still run). Tests and acceptance runs use it, so they
    never touch the real dconf/gsettings or the live Noctalia. With `--no-run`, a dry run also omits `run` Changes
    (pending ones stay recorded via `run_pending`); the T4 retry test asserts this.
  - With `terminal=true` it runs inline when a TTY is present. In the GUI it
  runs inside `wezterm start --always-new-process -- sh -c '...; read -p done'` and waits for exit. Packages use the
  same path: `sudo pacman -S --needed --noconfirm` (never `-Syu`), falling back to `paru`/`yay` for packages that failed.
- A dry run never executes `run when=build`. On a build-cache miss it reports `needs_apply=None` ("not built yet")
  instead of building.
- `niri_snippet` is not an op. After all components are applied, `cfg/rice.kdl` is regenerated as a whole file from
  the snippets of the enabled components (v1: only `cursor`).
- `GENERATORS = {"noctalia_palette": palette.build}` lives in `cli.py` and is passed into `recipe.apply`, so `recipe`
  never imports `palette`.

**State** (`$XDG_STATE_HOME/niri-noctalia-rice/state.json`, atomic writes):
`{id: {variant, refs: {lock_id: ref}, recipe_hash, whole: {relpath: sha256}, seeded: [relpath], patched: [relpath],
prev: {key: old_value}, run_pending: bool}}`. `run_pending` becomes true when `when=apply` steps are skipped by
`--no-run` or a command exits non-zero, and false after all of them succeed. `status.needs_apply` is true while it
is set. `prev` stores values that `off_steps` restore. Example: `palette` records the
`custom_palette` that was active before it was enabled.
A component is enabled if and only if it has an entry without `disabled: true`. Turning a component off writes
`{disabled: true}`, and the entry **keeps its `whole`/`seeded`/`patched` lists**, so `owned()` still includes those paths and
`install.sh` does not reset them. `--defaults` = enabled ∪ (`default=true` and no state entry at all). A component the user
turned off stays off.

**On/off semantics**
- Turning off a component with whole files leaves the files in place and replaces the entry with
  `{disabled: true}`. The files become the user's.
- A component with `off_steps` runs them when turned off. The `palette` off steps restore the `custom_palette` recorded in
  `prev` (fallback `"Catppuccin-Lavender"`). `qt` puts back `QT_QPA_PLATFORMTHEME "gtk3"`. `niri-look` sets the radius back to 20.
  `fonts` restores Adwaita Sans. `cursor` removes its snippet, because `rice.kdl` is regenerated.
- A component with patches and no `off_steps` is labelled **"turning off keeps current settings"** in the GUI.
  This applies to `kde-apps` and to the `apps` group run-steps (greeter, Handy, base packages).
- Switching variants: each variant's steps fully define its state, including the counterpart. `gtk=noctalia` adds
  `"gtk3"` and `"gtk4"` to `builtin_ids`, writes the repo `gtk.css` (`@import url("noctalia.css");`), sets settings.ini and
  gsettings to `adw-gtk3-dark`. `gtk=colloid` does the opposite. The starship variants work the same way with
  `"starship"`.

**Ownership:** `owned()` returns every whole-file, seed, and patch target in state. Whenever state exists it also
includes `.config/niri/cfg/rice.kdl`. The repo keeps a comment-only `home/.config/niri/cfg/rice.kdl`, so the
`include` target exists on a fresh install before rice runs. `install.sh`'s `install_tree` skips owned paths and
prints `kept (owned by rice): <path>`, so reruns stop resetting them. To get the repo version back,
`python -m rice refresh <relpath>` copies `home/<relpath>` over the live file (with a backup) and re-applies the
enabled patches that target it. Files that become rice outputs move out of `home/`:
- `fish/config.fish` is now generated. The user-specific lines (paths, env vars, completions) go to
  `home/.config/fish/conf.d/rice-local.fish`.
- `starship.toml` moves to `mokka/files/starship-noctalia.toml`.
- `fastfetch/config.jsonc` moves to `mokka/files/fastfetch-repo.jsonc` (the `fastfetch=repo` variant).

## 3. Frozen API contract (T4 implements it; T7 codes against it from day one)

```python
# rice/recipe.py
@dataclass class Variant: name; title; packages: dict; steps: list
@dataclass class Component: id; title; description; group; default; conflicts; requires; packages: dict
    only_if_installed: str|None; previews: list[str]; sources: list[str]; steps; off_steps: list|None
    niri_snippet: str|None; variants: dict[str, Variant]; default_variant: str|None; path: Path
    reversible: bool  # derived: no patches, or off_steps present
# rice/state.py
@dataclass class Status: enabled: bool; variant: str|None; needs_apply: bool|None  # None = not built yet
    stale_sources: list[str]
    recipe_changed: bool; modified: list[str]; missing_packages: list[str]; app_missing: bool
    update_available: bool|None  # None unless remote=True
# rice/cli.py
@dataclass class Change: relpath: str; kind: str  # new|changed|binary|run
    diff: str|None
@dataclass class Result: status: str  # applied|unchanged|skipped|modified|failed
    changes: list[Change]; message: str
components() -> list[Component]
lock(pkgbuilds: str, write: bool = True) -> dict[str, tuple[str|None, str]]
    # pkgbuilds is required: a commit or "latest" (no default). Returns the moved pins.
Generator = Callable[[dict[str, Path], Path], dict[str, Path]]   # (sources, out_dir) -> {relpath: file}
# recipe.render_whole(...) -> dict[str, tuple[Path, bool]]  {relpath: (path, track)}; state.install_whole consumes it
fetch(ids: list[str]) -> None
apply(ids: list[str], variants: dict[str, str] | None = None, dry_run=False, force=False, packages=True,
      run=True, log=print) -> dict[str, Result]
    # Not dry_run: fetch.ensure() any missing sources of the selected components first (network only if missing).
    # dry_run == diff: writes nothing, no network, no build runs. On a cache miss -> needs_apply=None.
disable(ids: list[str], log=print) -> dict[str, Result]
status(remote=False) -> dict[str, Status]
update(yes=False, log=print) -> tuple[dict[str, tuple], dict[str, Result]]  # lock(latest) -> affected -> apply
owned() -> list[str]
refresh(relpath: str, log=print) -> dict[str, Result]
```

CLI commands: `lock [--pkgbuilds C|--latest]`, `fetch [ids|--defaults]`,
`apply [ids|--defaults] [--with a,b] [--without a,b] [--variant id=name] [--dry-run] [--force] [--no-packages] [--no-run] [--yes]`,
`disable ids`, `status [--remote]`, `update [--yes]`, `owned`, `refresh <relpath>`, `gui [--smoke]`.
There is no separate `build` or `diff` command; `apply --dry-run` is the diff. A clean dry run prints `0 changes`
and exits 0. `--defaults` means enabled components
plus `default=true` components, **excluding the apps-group run-step components** (`greeter`, `handy`,
`base-packages`). Those run only when named explicitly or toggled in the GUI. `lock` requires `--pkgbuilds C` or
`--latest`; a bare `lock` is an error.

## 4. Resolution of SPEC §9 open questions

1. **Port install.sh steps into components: yes, in v1, without new ops.** The user wants the GUI to choose apps.
   - `apps` group recipes: `firedragon` (`packages.aur`, off by default), `nvim` (packages + `copy repo:nvim ->
     .config/nvim`), and `agents` (`copy` + `replace @HOME@` from `repo:agents/{claude,codex}`).
   - `greeter`, `handy`, and `base-packages` are `run terminal=true` steps that call `install.sh --only <step>`. They
     are `default=false`, so `apply --defaults` from install.sh never re-runs them, and they are labelled irreversible.
   - `install.sh` drops its own nvim/agents/firedragon code and maps `--no-nvim`/`--no-agents`/`--firedragon` to
     `rice apply --without/--with`.
2. **Fastfetch kitty logo:** add `config.enable_kitty_graphics = true` to `wezterm.lua` and keep the upstream
   `"type": "kitty"`.
3. **Static Kvantum Mokka with Noctalia colors: accept.** The Qt card says "Kvantum stays Mokka if you switch the
   Noctalia palette".
4. **Upstream check:** on demand only (`status --remote`, the GUI button). No timer.

## 5. Task breakdown

Parallelism:
- **Wave 1:** T1, T2, T3, T5 in parallel (disjoint files; T2 gets generators injected).
- **Wave 2:** T4, with T7 in parallel against §3 stubs.
- **Wave 3:** T6.
- **Wave 4:** T8.

Every task must leave `python -m unittest discover -s tests -v` green and offline.

### T1 - PKGBUILD parsing and lock
- **Files:** `rice/__init__.py`, `rice/lock.py`, `tests/test_lock.py`, `tests/fixtures/pkgbuilds/{garuda-mokka,garuda-fish-config,garuda-starship-prompt,garuda-wallpapers}/PKGBUILD` (trimmed real files), `mokka/upstream.lock.toml`
- **Deps:** none
- **Steps:**
  1. `parse_pkgbuild(text)` reads only column-0 assignments and stops at the first `^\w+\(\)\s*\{`. It handles
     scalars (`pkgver`, `_commit`, `pkgname`) and arrays (`source=(...)`, `sha256sums=(...)`) spanning lines, with
     quoted and unquoted items (`git+https://...#commit=X`). It expands `${var}`/`$var` from scalars parsed earlier.
     It asserts `len(source) == len(sha256sums)`. No bash execution and no general bash grammar.
  2. Classify sources:
     - `gitlab-archive` (`/-/archive/<commit>/`): sha from the PKGBUILD.
     - `git` (`git+https://github.com/o/r(.git)#commit=X` -> `url=https://github.com/o/r.git, ref=X`): no sha.
     - `raw-file` (`done.fish`, ref = the tag in the URL): sha from the PKGBUILD.
     - `pkgbuilds-path` (bare filename -> `https://gitlab.com/garuda-linux/pkgbuilds/-/raw/<commit>/<pkg>/<file>`):
       sha from the PKGBUILD.
     `install.sh` in garuda-mokka is a local helper and is dropped.
  3. `make_lock(commit, read_pkgbuild)` writes these ids: `garuda-mokka`, `catppuccin-{kde,micro,conky,konsole,bat,grub}`,
     `colloid`, `garuda-wallpapers`, `done`, `garuda-fish-config`, `garuda-starship-prompt`. Fields:
     `url, kind, ref, sha256?, derived_from="<pkg>/PKGBUILD:<line>"`. A tiny writer emits flat TOML (strings only).
     `--latest` resolves `main` via `https://gitlab.com/api/v4/projects/garuda-linux%2Fpkgbuilds/repository/commits/main`.
  4. Commit the real lock generated at `3d68043b106a6fa34ce7f8fbe96349105556bdcd`.
- **Acceptance:** `python -m unittest tests.test_lock -v` passes. The tests assert:
  - garuda-mokka: ref `7e0a1c7503a79454782d082fda38f8af89be608f`, sha `d0089ec5…`, 9 sources parsed.
  - colloid: kind `git`, ref `fd805dbe…`, no sha.
  - done: URL contains `/1.21.1/`, sha `402a83d9…`.
  - config.fish: kind `pkgbuilds-path`, sha `23f4d025…`.
  - wallpapers: ref `a0f80847…`.
  - A fixture test with an extra indented `depends=(...)`/`install -D...` inside `package()` shows they are ignored.
  - `python -c "import tomllib;d=tomllib.load(open('mokka/upstream.lock.toml','rb'));assert d['garuda-mokka']['ref'].startswith('7e0a1c7')"` exits 0.

### T2 - Recipe loader and ops
- **Files:** `rice/recipe.py`, `tests/test_recipe.py`
- **Deps:** none
- **Steps:**
  1. `load(path) -> Component` with tomllib. Validate the enums, op names, source prefixes, variants, and
     `default_variant`, and derive `reversible`. Errors name the file and field.
  2. `recipe_hash(component, variant)` is the sha256 of canonical JSON.
  3. `render_whole(component, variant, sources, repo_root, out_dir, generators)` handles `copy` (file or dir,
     `track`), `generate`, and `run when=build` (in a scratch copy of the source tree; argv list, no shell). It
     returns `{relpath: (path, track)}`.
  4. `patch(text, op, home) -> text` for `replace` (regex, `$HOME` substituted), `drop_lines`, `ini_set`, `toml_set`
     (literal/add/remove, multi-line arrays by bracket matching), and `json_set` (dotted keys). Each is a pure
     function. `patch_ops(component, variant)` groups the ops by target.
- **Acceptance:** `python -m unittest tests.test_recipe -v` passes, with at least one test per op, including:
  - `ini_set` on a real `kdeglobals` fixture keeps untouched lines byte-identical.
  - `toml_set` on a copy of `home/.config/noctalia/config.toml` changes only `custom_palette`, and `tomllib.loads`
    of the result succeeds.
  - `{remove="gtk3"}`, then `{remove="starship"}`, then `{add="gtk3"}` gives the expected `builtin_ids`.
  - `ini_set` on a missing file, and on a missing section, creates them.
  - Copy+patch: a `copy` of a fixture `config.fish` plus `drop_lines` on the same target gives `render_whole` output
    that already lacks the dropped lines, and the recorded sha is the sha of the patched text.
  - Every patch applied twice gives the same text as applied once.
  - `drop_lines` on the real `config.fish` fixture removes `upd|fixpacman|rmpkg|mirror*|grubup`.

### T3 - State and installer
- **Files:** `rice/state.py`, `tests/test_state.py`
- **Deps:** none
- **Steps:**
  1. Load and save `state.json` (schema in §2, `os.replace`).
  2. `install_whole(files, home, recorded, ts, force) -> list[Change]`:
     - Skip a file whose live sha equals its staged sha.
     - If the live file differs from both the recorded sha and the staged sha and `force` is not set, report it as
       `modified` and don't write it.
     - Otherwise back up a differing live file to `*.bak-<ts>` and write the staged file.
  3. `write_patched(relpath, new_text, home, ts)` writes only if the text changed, with a backup.
  4. `unified(old, new, relpath)`, `owned(state)`, and `Status` assembly helpers.
- **Acceptance:** `python -m unittest tests.test_state -v` passes. The tests show:
  - A second install into a temp HOME gives 0 `*.bak-*` and every file reports unchanged.
  - A hand-edited tracked file is not overwritten without force and is overwritten with force (with one backup).
  - A `track=false` file is never reported as modified.
  - **Seed:** seed a file, mutate the live file, and apply again with the same variant: no write, no `.bak`.
    Switching the variant re-seeds it.

### T4 - Fetch, orchestration, CLI
- **Files:** `rice/fetch.py`, `rice/cli.py`, `rice/__main__.py`, `tests/test_fetch.py`, `tests/test_cli.py`, `tests/fixtures/upstream/...`
- **Deps:** T1, T2, T3, T5 (generator)
- **Steps:**
  1. `fetch.ensure(entry) -> Path`: if the cache dir `upstream/<id>/<ref>/` exists, return it (no network). For a
     `git` source when `shutil.which("git")` is None, raise `git is required to fetch <id> (pacman -S git)`.
     - `git`: `git init` in a tmp dir, then `git fetch --depth 1 <url> <ref>`, then `git checkout FETCH_HEAD`. Assert
       `git rev-parse HEAD == ref`, drop `.git`, rename into place.
     - `gitlab-archive`: urllib to `.part`, verify sha256, `tarfile` with `filter="data"`, strip the top dir.
     - `raw-file`/`pkgbuilds-path`: urllib, verify sha256, save as a single file.
  2. `apply` (not dry run) first calls `fetch.ensure` for every missing source of the selected components. Then,
     per component, in recipe-file order:
     - Skip (`skipped`) if `only_if_installed` is missing.
     - Install packages, unless `dry_run` or `packages=False`.
     - Run `render_whole`, using the cache key. Copy-target patches and source-tree patches are applied inside it
       (§2).
     - For each live-patch target: read the live text (or the repo copy), apply its ops, and compare.
     - For a dry run, return the Changes. Otherwise call `install_whole` + `write_patched`, then run `when=apply`
       steps if the §2 trigger holds and `run=True`, then regenerate `rice.kdl`, then write state.
     - A failed component is reported and the rest continue. The exit code is 1 if any failed.
  3. Implement `disable`, `status` (`needs_apply` comes from a dry run, so no network unless `remote`), `update`
     (lock(latest, write=False) -> moved pins -> affected components = those with a moved source -> show dry run ->
     write lock and apply when confirmed or with `--yes`), `refresh`, `owned`, and argparse per §3.
- **Acceptance:**
  - `python -m unittest tests.test_fetch tests.test_cli -v` passes.
  - Fetch tests: a `file://` tarball with a good sha and with a bad sha (raises); a local `git` repo fetched by commit.
  - `test_cli` end-to-end with temp HOME/XDG dirs and a fixture lock + fixture components:
    - Apply twice: the second run reports all `unchanged`, there are 0 `.bak`, and `status` is clean.
    - **Run steps:** a fixture `run when=apply` command appends to a counter file. It runs once on the first apply,
      0 times on the second apply, and once after a recipe change.
    - **Fetch:** `apply` with an empty cache and a `file://` lock fetches, then applies. `apply --dry-run` with an
      empty cache does no fetch and reports `needs_apply is None`.
    - **Defaults stickiness:** after `disable X` (with `X` `default=true`), `apply --defaults` leaves `X` off.
    - A clean `apply --dry-run` prints `0 changes` and exits 0.
    - **Retry:** `apply --no-run`, then a normal `apply`, gives run count > 0 and `run_pending` false. A failing
      command leaves `run_pending` true, and `status` shows `needs_apply`.
    - `apply --defaults` never selects a component from the apps-group run steps.
    - `dry_run` on a build-cache miss does not run `when=build` and reports `needs_apply is None`.
    - **Two shared-file components** both patch one `config.toml`, and both edits survive.
    - Simulated Noctalia rewrite of a color key in a patch target: `status` stays clean.
    - **Selective update:** lock v2 moves one `git` source to a second commit of a `file://` repo. `status` flags
      only the component using it, and `update --yes` reports `applied` for it alone.
    - `disable` of a reversible component restores the counterpart. `rice.kdl` loses the snippet.
  - `python -m rice --help` lists exactly the §3 commands.

### T5 - Noctalia palette generator
- **Files:** `rice/palette.py`, `tests/test_palette.py`, `tests/fixtures/upstream/{Mokka.colors,catppuccin-mocha.colorscheme,catppuccin-latte.colorscheme}`
- **Deps:** none
- **Steps:**
  - `build(sources, out_dir)` writes `.config/noctalia/palettes/Mokka.json` in the exact shape of
    `Catppuccin-Lavender.json`.
  - Mokka.colors values like `"30, 30, 46"` are parsed by splitting on `,` and stripping spaces.
  - The terminal block comes from the catppuccin/konsole `catppuccin-{mocha,latte}.colorscheme`: `Foreground`,
    `Background`, `Color0..7` -> normal, `Color0Intense..7Intense` -> bright. `selectionBg` is Surface2, and
    `cursor` is Rosewater.

  Role table (dark = `Mokka.colors [section] key`, or a Mocha constant; light = Catppuccin Latte constants, cited in a module comment):

  | role | dark source | dark | light |
  |---|---|---|---|
  | mPrimary | `[Colors:Selection] BackgroundNormal` | #cba6f7 | #8839ef |
  | mOnPrimary | `[Colors:Selection] ForegroundNormal` | #1e1e2e | #eff1f5 |
  | mSecondary | `[Colors:Window] DecorationHover` | #b4befe | #7287fd |
  | mOnSecondary | `[Colors:Selection] ForegroundInactive` | #181825 | #eff1f5 |
  | mTertiary | `[Colors:Window] ForegroundActive` | #fab387 | #fe640b |
  | mOnTertiary | `[Colors:Selection] ForegroundInactive` | #181825 | #eff1f5 |
  | mError | `[Colors:Window] ForegroundNegative` | #f38ba8 | #d20f39 |
  | mOnError | `[Colors:Selection] ForegroundInactive` | #181825 | #eff1f5 |
  | mSurface | `[Colors:Window] BackgroundNormal` | #1e1e2e | #eff1f5 |
  | mOnSurface | `[Colors:Window] ForegroundNormal` | #cdd6f4 | #4c4f69 |
  | mSurfaceVariant | `[Colors:Button] BackgroundNormal` | #313244 | #ccd0da |
  | mOnSurfaceVariant | `[Colors:Window] ForegroundInactive` | #a6adc8 | #6c6f85 |
  | mOutline | Mocha Surface2 constant | #585b70 | #acb0be |
  | mShadow | Mocha Crust constant | #11111b | #dce0e8 |
  | mHover | `[Colors:Window] DecorationFocus` | #cba6f7 | #8839ef |
  | mOnHover | Mocha Crust constant | #11111b | #eff1f5 |
- **Acceptance:** `python -m unittest tests.test_palette -v` passes. The test checks:
  - The key sets equal those of `home/.config/noctalia/palettes/Catppuccin-Lavender.json`, recursively.
  - The dark column equals the table above.
  - Every value matches `^#[0-9a-f]{6}$`.
  - `build()` returns exactly `{".config/noctalia/palettes/Mokka.json": <path>}`, and that path exists.

### T6 - Recipes and repo changes
- **Files:**
  - `mokka/components/*.toml`. Look: `palette, fonts, cursor, niri-look`; qt: `qt, kde-apps`; gtk: `gtk`;
    shell: `fish, starship, fastfetch`; terminal: `konsole-kate, yakuake, bat, micro`; wallpapers: `wallpapers`;
    apps: `nvim, agents, firedragon, greeter, handy, base-packages`.
  - `mokka/files/{starship-noctalia.toml, fastfetch-repo.jsonc, gtk-colloid.css}` (moved/new).
  - Repo edits: delete `home/.config/fish/config.fish`; new `home/.config/fish/conf.d/rice-local.fish`;
    `home/.config/niri/config.kdl` gets `include "./cfg/rice.kdl"` before `noctalia.kdl`; new
    `home/.config/niri/cfg/rice.kdl` (comment only); `home/.config/wezterm/wezterm.lua` gets
    `enable_kitty_graphics`; `home/.config/gtk-{3,4}.0/settings.ini` lose capitaine/Fira.
  - `tests/test_recipes_data.py`.
- **Deps:** T4 (to validate), T5 (palette). Recipes can be drafted against §2 during wave 2.
- **Notes (beyond SPEC §5):**
  - `qt`: `ini_set` `.config/Kvantum/kvantum.kvconfig [General] theme=Mokka` and copy the Kvantum dirs.
    `ini_set` `.config/qt6ct/qt6ct.conf [Appearance] style=kvantum-dark, custom_palette=true,
    color_scheme_path=$HOME/.config/qt6ct/colors/noctalia.conf` (output of the `qt` template) and `[Fonts]`. `replace` `misc.kdl`
    `QT_QPA_PLATFORMTHEME "gtk3"` -> `"qt6ct"`, with `off_steps` reversing it.
  - `kde-apps`: install `Mokka.colors` to `.local/share/color-schemes/` (installed, not selected).
    `ini_set kdeglobals`: `[General] font/menuFont/smallestReadableFont/toolBarFont=Inter…Bold`,
    `fixed=JetBrainsMono Nerd Font,10…`, `[KDE] widgetStyle=kvantum-dark`, `[Icons] Theme=Tela-circle-dracula-dark` (**our addition**: upstream kdeglobals has no
    `[Icons]`; the value matches the upstream GTK settings.ini).
    Never `ColorScheme`, `LookAndFeelPackage`, or `[Colors:*]`. Selected `dolphinrc` keys.
  - `gtk`: variants as in §2. `colloid` also runs `replace` on the scratch copy (drop `clean_theme && `), then
    `run when=build` `./install.sh -n Catppuccin -t purple -c dark --tweaks catppuccin -d <out>/.themes`, then
    `gtk-theme-name=Catppuccin-Purple-Dark-Catppuccin` in both settings.ini and gsettings, plus
    `{remove="gtk3"}` + `{remove="gtk4"}` (the `noctalia` variant adds both back). Its GUI notes: "runs upstream build script; libadwaita apps ignore it".
  - `starship`: `default_variant = "mokka"`. `mokka`/`garuda` copy whole and `{remove="starship"}`. `noctalia` seeds (`copy track=false`) from
    `repo:mokka/files/starship-noctalia.toml` and `{add="starship"}`.
  - `nvim`: `copy repo:nvim -> .config/nvim` (tracked), except `lazy-lock.json`, which is seeded (`track=false`).
  - `fastfetch`: `mokka` copies `usr/share/fastfetch/presets/mokka.jsonc` -> `.config/fastfetch/config.jsonc` and
    `replace`s the logo path with `$HOME/.local/share/icons/garuda/mokka-fastfetch.png`. It also does
    `copy garuda-mokka:usr/share/icons/garuda/mokka-fastfetch.png -> .local/share/icons/garuda/`. `repo` copies the moved file.
  - `fish`: `copy` of `garuda-fish-config:config.fish`, then copy-target `drop_lines` (`upd`, `fixpacman`, `rmpkg`,
    `mirror*`, `grubup`), plus `replace` to add `--icons --color=always` to the eza aliases. Copy `done.fish` -> `.config/fish/conf.d/`
    (auto-sourced, like `rice-local.fish`). The `__garuda_fastfetch` function definition stays; it is harmless because
    its caller is in `stub.fish`, which we don't install.
  - `cursor`: the theme is `Catppuccin-Mocha-Mauve-Cursors` (upstream `index.theme` casing; the AUR package may
    install it in lowercase, which is not verified).
    - Before writing anything, the step resolves the theme dir **case-insensitively** under `/usr/share/icons` and
      `~/.local/share/icons`, and every written value uses the name found on disk.
    - If the dir is missing: with `--no-packages`, warn and skip the component (exit 0); otherwise fail with
      "cursor theme dir not found".
    - `niri_snippet = 'cursor { xcursor-theme "<found name>"; xcursor-size 24; }'`.
    - Also: `ini_set` in both settings.ini, `gsettings cursor-theme`, and `copy garuda-mokka:etc/skel/.icons/default/index.theme`.
  - `niri-look`: `replace` `rules.kdl` `geometry-corner-radius 20 // Set every window` -> `16`, with an `off_steps`
    inverse. The focus-ring stays with `noctalia.kdl`.
  - `palette`: `generate noctalia_palette`, `toml_set theme.custom_palette="Mokka"`, and fix the legacy template
    ids in `theme.templates.builtin_ids`: `{remove="gtk"}`, `{remove="kde"}`, `{add="gtk3"}`, `{add="gtk4"}`,
    `{add="kcolorscheme"}` (F4). Then
    `run noctalia msg templates-apply` (no `|| true`; a failure sets `run_pending`, and the next apply retries), with `off_steps` restoring `Catppuccin-Lavender`.
  - `fonts`: packages `inter-font`, `ttf-jetbrains-mono-nerd`. `toml_set shell.font_family="Inter"`. `ini_set`
    both settings.ini `gtk-font-name=Inter 10`. `gsettings font-name 'Inter 10'` and
    `monospace-font-name 'JetBrainsMono Nerd Font 10'`. `off_steps` restore Adwaita Sans/Mono. Mokka
    `fontconfig/fonts.conf` is not installed.
  - `konsole-kate` (`only_if_installed` konsole or kate):
    - copy `garuda-mokka:usr/share/konsole/Mokka.colorscheme` and `catppuccin-konsole:themes/catppuccin-mocha.colorscheme`
      -> `.local/share/konsole/`, with a copy-target `replace` of `Opacity=1` -> `Opacity=0.9` (as in the PKGBUILD);
    - copy `etc/skel/.local/share/konsole/Garuda.profile`;
    - copy `usr/share/org.kde.syntax-highlighting/themes/Mokka.theme` -> `.local/share/org.kde.syntax-highlighting/themes/`.
  - `yakuake` (`only_if_installed yakuake`): copy `usr/share/yakuake/skins/mocha` -> `.local/share/yakuake/skins/mocha`,
    and `ini_set .config/yakuakerc [Appearance] Skin=mocha`.
  - `bat`: copy `catppuccin-bat:themes/Catppuccin Mocha.tmTheme` -> `.config/bat/themes/` and
    `garuda-mokka:etc/skel/.config/bat/config` -> `.config/bat/config`, then `run bat cache --build`.
  - `micro`: copy `catppuccin-micro:themes/catppuccin-mocha.micro` -> `.config/micro/colorschemes/`, and
    `json_set .config/micro/settings.json colorscheme="catppuccin-mocha"`.
  - `wallpapers`: variants `mokka` (City-Horizon, Mokka-tree -> `~/Pictures/Wallpapers/garuda/`, default),
    `mokka-default` (also `toml_set wallpaper.default.path`), and `full` (+ garuda-wallpapers `src/garuda-wallpapers/*`).
- **Acceptance (exact):**
  ```
  python -m rice fetch --defaults
  T=$(mktemp -d); mkdir -p $T/.config; cp -r home/.config/{niri,noctalia,gtk-3.0,gtk-4.0} $T/.config/
  grep -rl @HOME@ $T/.config | xargs -r sed -i "s|@HOME@|$T|g"
  mkdir -p $T/.local/share/icons/Catppuccin-Mocha-Mauve-Cursors/cursors   # fixture cursor theme (V1)
  XDG_CACHE_HOME=~/.cache XDG_STATE_HOME=$T/.state HOME=$T python -m rice apply --defaults --no-packages --no-run   # exit 0
  niri validate -c $T/.config/niri/config.kdl                                                               # ok
  ! grep -E 'rmpkg|fixpacman|grubup|garuda-update' $T/.config/fish/config.fish && fish -n $T/.config/fish/config.fish
  grep -q 'QT_QPA_PLATFORMTHEME "qt6ct"' $T/.config/niri/cfg/misc.kdl
  grep -q xcursor-theme $T/.config/niri/cfg/rice.kdl
  grep -A12 'match app-id="\^steam_app_"' $T/.config/niri/cfg/rules.kdl | grep -q 'geometry-corner-radius 0'
  python -c "import json;json.load(open('$T/.config/noctalia/palettes/Mokka.json'))"
  python -c "import tomllib;c=tomllib.load(open('$T/.config/noctalia/config.toml','rb'))['theme'];assert c['custom_palette']=='Mokka';ids=c['templates']['builtin_ids'];assert {'gtk3','gtk4','kcolorscheme'}<=set(ids) and not {'gtk','kde'}&set(ids)"
  test -f "$T/.config/bat/themes/Catppuccin Mocha.tmTheme"
  python -c "import json;assert json.load(open('$T/.config/micro/settings.json'))['colorscheme']=='catppuccin-mocha'"
  grep -q "$T/.local/share/icons/garuda/mokka-fastfetch.png" $T/.config/fastfetch/config.jsonc
  test -f $T/.local/share/icons/garuda/mokka-fastfetch.png
  grep -q 'palette = "catppuccin_mocha"' $T/.config/starship.toml
  XDG_CACHE_HOME=~/.cache XDG_STATE_HOME=$T/.state HOME=$T python -m rice apply --defaults --no-packages --no-run --dry-run | grep -qx '0 changes'
  ```
  `python -m unittest tests.test_recipes_data -v` passes. It checks that every recipe loads, every source id exists
  in the lock, and **no `ini_set` in `qt`/`kde-apps` touches a `Colors:*` section or a `ColorScheme` /
  `LookAndFeelPackage` key**. It also includes a **template-id pre-check**: every id that any recipe writes into
  `builtin_ids` must exist as `[templates.<id>]` in `/usr/share/noctalia/assets/templates/builtin.toml` (the check
  is skipped if that file is absent).

### T7 - GTK4/libadwaita GUI
- **Files:** `rice/gui.py`, `tests/test_gui_smoke.py`, `mokka/niri-noctalia-rice.desktop`
- **Deps:** §3 contract (starts in wave 2 against stubs); integration after T4.
- **Steps:**
  1. `Adw.NavigationSplitView`: the sidebar lists the 7 groups. Each component is an `Adw.SwitchRow`, or an
     `Adw.ComboRow` for variants.
  2. Badges: needs package X (AUR), app not installed (skipped), update available, edited by hand, needs apply.
     Notes: "turning off keeps current settings" when `not reversible`, and "runs upstream build script" (colloid).
  3. `Adw.Carousel` of `Gtk.Picture`:
     - Sources: `mokka/previews/<id>/*.png`, then the recipe `previews` resolved in the cache (e.g. Mokka-tree
       `contents/screenshot.jpg`).
     - The `palette` card shows a swatch of colored `Gtk.Box`es built from the generated JSON.
     - Missing previews show a "Fetch to see preview" placeholder.
  4. Header actions:
     - "Check for updates" calls `status(remote=True)`.
     - Previews of components whose sources are missing show "Fetch to see preview". A "Fetch" action calls `fetch(ids)`.
       "Apply" fetches implicitly (F1), with progress in the log.
     - "Show changes" calls `apply(dry_run=True)` and opens a dialog with a monospace `Gtk.TextView`.
     - "Apply" calls `apply`/`disable` for the toggled rows in a thread. `log` is wired to `GLib.idle_add`, and a
       per-component report is shown at the end.
  5. `--smoke`: build the window, visit every group page, load at least one `Gtk.Picture` from
     `mokka/previews`, falling back to the bundled placeholder picture when none exist yet (before T8 adds
     screenshots), and quit on first idle with exit code 0 (exit code 1 if no picture loaded).
- **Acceptance:** `python -m rice gui --smoke` exits 0 in the niri session. `tests/test_gui_smoke.py` runs it in a
  subprocess and skips without `WAYLAND_DISPLAY`. Then manual M5.

### T8 - install.sh integration, previews, docs, manual verification
- **Files:** `install.sh`, `README.md`, `mokka/capture-previews`, `mokka/previews/*/*.png`
- **Deps:** T4, T6, T7
- **Steps:**
  1. `install_tree` skips every path in `python -m rice owned` (read once into an associative array) and prints
     `kept (owned by rice): <path>` for each.
  2. `--only a,b` (steps `pkgs|greeter|handy`): set all OPT=0 except the listed ones, then skip prompts,
     `install_tree`, and gsettings. **`install.sh --only` never calls rice**, so there is no recursion when rice
     calls `install.sh --only`. Fixes:
     - (a) the `/Handy/d` keybind `sed` runs only when `handy` was requested and then declined or failed, never for
       other `--only` steps;
     - (b) `outs` (connected outputs) is computed unconditionally, near the top, before any step that uses it
       (greeter, DP-2 pin);
     - (c) the pkgs step uses `sudo pacman -S --needed --noconfirm` **always**, not only under `--only`; the `-Syu`
       is dropped, per the "never -Syu" principle, and README says to update the system first.

     `REPO_PKGS` loses the nvim/firedragon extras (now in recipes), gains `git` (needed by `fetch`), and
     `capitaine-cursors` is replaced by `inter-font`.
  3. Remove gsettings `gtk-theme`/`cursor-theme`/`font-name`/`monospace-font-name` (now owned by
     `gtk`/`cursor`/`fonts`). Keep `icon-theme` and `color-scheme`. Guard the Handy autostart append with
     `grep -q '.local/bin/handy' || printf …`.
  4. Add `ask mokka … y` and `--no-mokka`/`--gui`. At the end run `python -m rice apply --defaults [--no-packages]
     [--without nvim,agents] [--with firedragon] --yes`. That call fetches missing sources itself (F1), so there is
     no separate `fetch` call. With `--gui`, run `python -m rice gui` instead. If `RICE_NO_RUN=1` is set, install.sh
     adds `--no-run`. Install the `.desktop` file.
  5. `mokka/capture-previews <id>`: apply the component, open a representative window, run
     `niri msg action screenshot-window`, and move the shot to `mokka/previews/<id>/`. Capture palette, qt (dolphin),
     konsole-kate, fish+starship (wezterm), fastfetch, both gtk variants, and niri-look.
  6. README: component list, commands, ownership (`rice owned`, the `kept (owned by rice)` message,
     `rice refresh <relpath>` to get the repo version back), "update the system before installing", and the §4
     decisions.
- **Acceptance:**
  - `bash -n install.sh` passes.
  - Preview check: for every component with a non-empty `previews` list (or a `mokka/previews/<id>/` dir), at
    least one file exists:
    `python -c "from rice import cli; ...assert every component has >=1 existing preview file"`, wrapped as
    `tests/test_previews.py::test_previews_exist`, added in **T8** (not T6), so earlier waves stay green. The `gui --smoke` run loads one `Gtk.Picture`.
  - Rerun check. **This touches the real `$HOME`** (install_tree, fish, Noctalia sed). `RICE_NO_RUN=1` keeps rice's
    run steps (gsettings, `templates-apply`) out of it. Run it on this machine only, after a backup or in a
    throwaway user:
    ```
    RICE_NO_RUN=1 ./install.sh --yes --no-pkgs --no-greeter --no-handy          # run 1
    touch /tmp/m; sha256sum $(python -m rice owned | sed "s|^|$HOME/|") > /tmp/s1
    RICE_NO_RUN=1 ./install.sh --yes --no-pkgs --no-greeter --no-handy          # run 2
    for p in $(python -m rice owned); do find "$HOME/$(dirname $p)" -maxdepth 1 -newer /tmp/m -name "$(basename $p).bak-*"; done   # prints nothing
    sha256sum $(python -m rice owned | sed "s|^|$HOME/|") | diff - /tmp/s1                                                       # no diff
    ```
  - `./install.sh --only handy` run twice leaves exactly one Handy line in `autostart.kdl`.
  - M1–M6 pass.

## 6. Verification plan

- **Unit** (`python -m unittest discover -s tests -v`, offline):
  - lock parsing on the 4 trimmed real PKGBUILDs, including the indented-lines-ignored case;
  - every op, and idempotence of every patch;
  - shared-file composition;
  - Noctalia color rewrite not flagged;
  - on/off and variant counterparts;
  - selective update with a 2-commit `file://` git repo;
  - palette role table;
  - recipe-data checks (no color keys);
  - fetch good/bad sha.
- **Idempotency in temp `$HOME`:** in `test_cli.py` (fixtures), and with real upstream per the T6 acceptance
  (`apply --no-run` twice, then `--dry-run` shows no changes, 0 `.bak`).
- **Offline:** after `fetch`, `unshare -rn python -m rice apply --defaults --no-packages --no-run --dry-run` succeeds.
- **GUI smoke:** `python -m rice gui --smoke`.
- **Manual on this machine** (record the results in the PR):
  - M1: `apply --defaults`, then `niri validate`, `noctalia msg templates-apply`. Noctalia shows the Mokka mauve
    accent, and `~/.config/kdeglobals` `[Colors:Window]` still comes from Noctalia.
  - M2: Dolphin/Kate/Konsole show Kvantum Mokka + Inter. `qt6ct` shows the kvantum-dark style and the noctalia colors.
  - M3: A new wezterm shows the mokka starship prompt. `type upd rmpkg fixpacman` prints "not found".
    `rice-local.fish` lines are active (`echo $PATH`).
  - M4: fastfetch renders the Mokka PNG logo in wezterm.
  - M5: In the GUI, set `gtk=colloid`, Show changes, Apply. GTK3 apps restyle. **Toggle back to `noctalia`:**
    `gtk.css` has the import again, `"gtk3"`/`"gtk4"` are back in `builtin_ids`, and gsettings is `adw-gtk3-dark`. Turn `qt`
    off: `misc.kdl` is back to `gtk3`. Turn `cursor` off: the snippet is gone from `rice.kdl`.
  - M6: `python -m rice lock --pkgbuilds <older pkgbuilds commit touching garuda-mokka/PKGBUILD>` (found via the
    GitLab commits API with `path=garuda-mokka/PKGBUILD`), then `python -m rice status`. Exactly the garuda-mokka
    components are flagged. Restore the lock with `git checkout mokka/upstream.lock.toml`.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| GitLab archive tarballs are not byte-stable, so the PKGBUILD sha stops matching | `fetch` fails hard with a clear message. Re-running `lock` re-pins. Cache dirs are keyed by ref, so trees already fetched keep working |
| Line-based INI/TOML editors corrupt files | Edits are limited to whole `key = value` lines and bracket-matched arrays. Tests re-parse with `tomllib` and assert untouched lines are byte-identical. Every write leaves a backup |
| Noctalia templates and rice fight over a file | Rice never writes color keys, and patch targets are not sha-tracked. Shared lists use `add`/`remove`. Starship and gtk variants hand the file to the template or take it back explicitly |
| Owned files no longer get repo updates via `install.sh` | `rice refresh <relpath>` is the documented escape, and README lists owned paths (`rice owned`) |
| Colloid `install.sh` (downloaded code) misbehaves | Opt-in variant labelled in the GUI. Runs in a scratch copy with `-d <out>`, from a pinned commit verified by git |
| AUR/chaotic packages missing elsewhere | pacman first, then paru/yay. Without a helper, the component reports `skipped: install X` and the rest continue |
| `install.sh --only` steps launched from the GUI need sudo | They run in a wezterm terminal with a TTY, are labelled irreversible, and are never default |

## 8. ADR

- **Decision:** `rice/` is a stdlib Python package that interprets declarative TOML recipes (8 ops) over upstream
  sources pinned from Garuda PKGBUILDs. One frozen API serves the CLI and the GTK4/libadwaita GUI. `install.sh` stays
  the bootstrap: it exposes `--only` steps for the GUI's apps group and calls `python -m rice apply`.
- **Drivers:** cheap updates with selective re-apply and a diff, structured metadata for the GUI, and minimal
  offline-testable code.
- **Alternatives considered:** per-component Python modules (B) and extending bash `install.sh` (C); see §1.
  Also considered and dropped: staging cache for patch ops (loses concurrent edits), sha tracking of patch targets
  (Noctalia rewrites them), window rules in an included `rice.kdl` (overrides the games rule), and TOFU sha for
  GitHub sources (the git commit already verifies content).
- **Why chosen:** the recipes double as GUI metadata and change-detection input. Patches applied live compose
  safely with Noctalia and with each other. Eight tested ops cover all 21 components.
- **Consequences:**
  - **Ownership:** every whole-file output and every patch target that rice has applied is owned. `install.sh` stops
    copying owned paths, and the only way back to the repo version is `rice refresh <relpath>`.
  - `fish/config.fish`, `starship.toml`, and `fastfetch/config.jsonc` leave `home/`. User fish additions live in
    `conf.d/rice-local.fish`.
  - Some components are irreversible, and the GUI labels them.
  - The lock is committed and changes only via `lock`/`update`.
  - There is one exception to "no downloaded code": Colloid.
  - `install.sh` gains `--only` and loses its nvim/agents/firedragon code, gsettings theme lines, and
    `capitaine-cursors`.
- **Follow-ups:**
  - Optional GRUB component (off by default).
  - Kvantum driven by a Noctalia template if palette switching matters.
  - Moving greeter/Handy into native recipes if a second user appears.
  - Revisit `garuda-icons` if fastfetch needs more logos.

## 9. Changelog v2 (review item -> where addressed)

| Item | Addressed in |
|---|---|
| P1 patches at apply time against live files; only whole outputs cached | §2 "8 ops", T2.4, T4.2, test in T4 (two shared-file components) |
| P2 no sha for patch files; status = re-apply is a no-op | §2 State, T3, T4 test (Noctalia rewrite not flagged) |
| P3 no window-rule in rice.kdl; radius via replace in rules.kdl; rice.kdl only cursor | §0.5, T6 `niri-look`/`cursor`, T6 acceptance (steam rule keeps radius 0) |
| P4 no ColorScheme/Colors:*; qt6ct points to Noctalia output; Mokka.colors installed not selected | §0.4, T6 `qt`/`kde-apps`, M1/M2 |
| P5 install.sh gsettings conflict; gtk template resets gtk-theme | §0.7, T8.3, T6 `gtk` (colloid removes the `gtk3`/`gtk4` templates) |
| P6 GENERATORS in cli.py, injected | §2, T2.3, wave 1 parallelism |
| P7 parser: column 0, stop at first function, unquoted git+, len check, indented-lines fixture | T1.1, T1 acceptance |
| P8 colloid drops gtk.css import, sets both settings.ini + gsettings; noctalia restores; libadwaita note | §2 On/off, T6 `gtk`, T7 note, M5 |
| P9 apps choice in v1 via `install.sh --only` + apps recipes, no new ops | §4.1, T6 apps recipes, T8.2, T8 acceptance |
| Antithesis: no build/diff steps; diff = apply(dry_run) | §1 P1, §3, T4 |
| C1 frozen API before T7; build/diff removed | §3, T7 deps |
| C2 cheaper QT override: replace in misc.kdl | §0.5, T6 `qt`, T6 acceptance grep |
| C3 test: no Colors:/ColorScheme in qt/kde-apps | T6 `tests/test_recipes_data.py` |
| C4 T8 acceptance rewritten; install_tree skips owned | §2 Ownership, T8.1, T8 acceptance |
| C5 ownership explicit, `refresh` escape, fastfetch moved | §2 Ownership, §8 Consequences |
| C6 on/off semantics, counterparts, irreversible labels, M5 toggle-back | §2 On/off, T7.2, M5 |
| C7 M6 replaced: fixture 2-commit test + real `lock --pkgbuilds <older>` | T4 test (selective update), M6 |
| C8 exact T6 acceptance with copied niri config in $T | T6 acceptance |
| C9 full 16-role table, comma+space parsing | T5 |
| Optional: git fetch by commit for GitHub, no TOFU | §0.2, T1.2, T4.1 |
| Optional: rice.kdl whole file from snippets, kdl_block dropped | §2 `niri_snippet`, T4.2 |
| Optional: regex-scoped PKGBUILD parsing | T1.1 |

## 10. Changelog v3 (review item -> where addressed)

| Item | Addressed in |
|---|---|
| N1 `track=false` = seed (write if missing or variant changed); starship noctalia + nvim `lazy-lock.json`; T3 test | §2 "8 ops" (seed), §2 State `seeded`, T6 `starship`/`nvim`, T3 acceptance (seed test) |
| N2 `run when=apply` only on enable / variant / hash / refs / file change; 0 commands on a second apply; apps run-steps excluded from `--defaults`; `--only` never calls rice | §2 run trigger, §3 `--defaults`, T4.2, T4 acceptance (counter test, defaults exclusion), T8.2 |
| N3 `owned()` always includes `cfg/rice.kdl` once state exists; comment-only repo copy | §2 Ownership, T6 files |
| N4 `--only` fixes: Handy sed scope, `outs` unconditional, `pacman -S --needed` always (no `-Syu`) | T8.2 (a)(b)(c) |
| N5 `git` in REPO_PKGS; clear error if missing | T8.2, T4.1 |
| N6 T6 acceptance uses repo `home/.config/*` with `@HOME@` -> `$T`; `xcursor-theme` grep | T6 acceptance |
| N7 dry run never runs `when=build`; `needs_apply=None` on cache miss; `lock()` has no default; `variants: dict|None = None` | §2, §3 (`Status`, `lock`, `apply`), T4 acceptance |
| R1 skipped/failed run steps are retried via `run_pending`; `|| true` dropped | §2 run trigger + State, T6 `palette`, T4 acceptance (retry test) |
| A1 hermetic `--no-run` used in tests and acceptance | §2 run, §3 CLI, T6 acceptance, §6 idempotency/offline |
| Optional: palette off restores previous `custom_palette` | §2 State `prev`, §2 On/off |
| Optional: `kept (owned by rice): <path>` + README `rice refresh` | §2 Ownership, T8.1, T8.6 |

## 11. Changelog v4 (verifier)

| Item | Addressed in |
|---|---|
| F1 `apply` fetches missing sources; dry run/status stay offline (`needs_apply=None`); test; install.sh/GUI paths | §3 `apply` comment, T4.2, T4 acceptance (Fetch), T8.4, T7.4 |
| F2 copy-target patches applied to staged text before hashing; source-tree patches before `run when=build`; only uncopied targets are live patches; test | §2 Patches, T4.2, T2 acceptance (copy+patch), T6 `fish`/`konsole-kate` |
| F3 qt template writes `qt6ct/colors/noctalia.conf`; KColorScheme at `~/.local/share/color-schemes/noctalia.colors` | §0.4, T6 `qt` |
| F4 real template ids `gtk3`/`gtk4`/`kcolorscheme`; `palette` fixes legacy `gtk`/`kde`; gtk variants add/remove both; builtin.toml pre-check test | §0.4, §2 variants, T6 `palette`/`gtk`, T6 acceptance + `test_recipes_data`, T2 test, M5 |
| F5 `__garuda_fastfetch` call claim removed (definition left, harmless) | T2 acceptance, T6 `fish` |
| F6 `ini_set` creates missing file/section; test | §2 `toml_set`/`ini_set` bullet, T2 acceptance |
| F7 frozen `Generator` and `render_whole` return shape | §3 |
| F8 `disabled: true` stickiness; `--defaults` = enabled ∪ (default and no entry) | §2 State + On/off, T4 acceptance |
| F9 clean dry run prints `0 changes`, exit 0; acceptance greps it | §3, T4 + T6 acceptance |
| F10 T8 rerun touches real `$HOME`; `RICE_NO_RUN=1` forwarded to `--no-run` | T8.4, T8 acceptance |
| F11 T4 and T6 depend on T5 | T4/T6 Deps |
| F12 `lock` signature fixed | §3 |
| F13 `[Icons]` marked as our addition | T6 `kde-apps` |
| F14 `Catppuccin-Mocha-Mauve-Cursors`; installed dir checked before writing | T6 `cursor` |
| Traceability: konsole-kate, yakuake, bat, micro, fonts notes; extended T6 asserts; preview acceptance | T6 notes + acceptance, T8 acceptance, T7.5 |
| V1 cursor dir resolved case-insensitively and the found name used; warn + skip with `--no-packages`; fixture dir in T6 acceptance | T6 `cursor`, T6 acceptance |
| V2 `test_previews_exist` added in T8; `--smoke` falls back to a placeholder picture until then | T7.5, T8 acceptance |
| V3 fastfetch copies `mokka-fastfetch.png`; acceptance checks it with `test -f` | T6 `fastfetch`, T6 acceptance |
| V4 T5 asserts `build()` return shape | T5 acceptance |
| V5 disabled entry keeps its file lists; `owned()` includes them | §2 State |
| V6 `toml_set`/`json_set` create missing files; starship `default_variant = "mokka"` | §2 ops, T6 `starship` |

## 12. Addendum A+B (2026-11-16, post-implementation): Noctalia 5.5 settings layer, user custom dir

Status: pending review (architect → critic). Found during live verification M1, after the system update to Noctalia 5.5.0.

### 12.0 Facts (verified on this machine)
- Noctalia 5.x has two config layers. `~/.config/noctalia/config.toml` is the base. `~/.local/state/noctalia/settings.toml`
  is written by the Noctalia settings UI and **overrides** it. Here it holds `[theme] custom_palette="Catppuccin-Lavender"`
  and full `builtin_ids`/`community_ids` lists. So `toml_set` on config.toml for `theme.*` is a no-op on any machine where
  Noctalia has run. `shell.font_family` is not in the state file yet, so it still comes from config.toml.
  `noctalia msg config-get theme.custom_palette` → `'Catppuccin-Lavender'`, `config-get shell.font_family` → `'Adwaita Sans'`.
- IPC: `noctalia msg color-scheme-get|color-scheme-set <builtin|wallpaper|community|custom> <name>` (persists to
  settings.toml), `config-get <path>`, `config-set <path> <value>`, `config-reload`.
- Noctalia 5.5 removed `starship` from builtin.toml. It is now a **community** template (`noctalia theme --list-templates`
  lists it under "Community templates (cached)"). `tests/test_recipes_data.py::test_builtin_ids_exist` fails on 5.5 for
  that reason.
- niri 26.04: `include optional=true "<path>"` is valid when the file is missing (WARN only). A later include's `binds`
  override earlier ones (checked with `niri validate`).

### 12.1 A: Noctalia settings through IPC
- New op `noctalia` (an apply-time step with the same trigger and `run_pending` semantics as `run when=apply`; shown as
  `run` Changes in a dry run; skipped by `--no-run`). Forms:
  - `{op="noctalia", color_scheme=["custom","Mokka"]}`
  - `{op="noctalia", set="shell.font_family", value="Inter"}`
  - `{op="noctalia", list_add="theme.templates.community_ids", item="starship"}` / `list_remove=…`
  Each form reads first (`color-scheme-get` / `config-get`), writes only if different, and fails (exit ≠ 0 → `run_pending`)
  if `noctalia` is missing or not running. The executor must determine the exact value syntax `config-set` accepts for
  strings and lists, and test it against the live binary. On enable the previous value is recorded in state `prev` (via the
  get call), and off_steps restore it.
- Recipes:
  - `palette`: keep `generate` + config.toml `toml_set` (fresh installs). Add `noctalia color_scheme=["custom","Mokka"]`
    and replace `run noctalia msg templates-apply` only if color-scheme-set already re-renders templates (verify). The
    legacy-id fix stays a config.toml patch and is mirrored with `list_remove`/`list_add` on `builtin_ids`.
  - `gtk`: the colloid/noctalia variants toggle `gtk3`/`gtk4` with `list_remove`/`list_add` (plus the existing config.toml patch).
  - `starship`: toggle `"starship"` in **`community_ids`** (config.toml patch + `noctalia` op). Remove it from
    `builtin_ids` handling entirely.
  - `fonts`: add `noctalia set shell.font_family Inter`.
- `test_recipes_data`: the id pre-check validates `builtin_ids` ids against builtin.toml and `community_ids` ids against
  `noctalia theme --list-templates` output (skip each when unavailable). It must pass on 5.5.

### 12.2 B: persistent user custom dir `~/.config/rice-custom/`
Goal: user additions that rice/install.sh never overwrite, back up, or own, and that are loaded **last** so they override.
- Never in `owned()`, never written by rice except **seeding**: on every `apply` (and `install.sh`), create the dir and the
  skeleton files below **only if missing** (`mokka/custom-skel/` → `~/.config/rice-custom/`, commented examples, no
  personal data). The GUI gets an "Open custom folder" button (`xdg-open`).
- Hooks, each loading custom content after the managed config:
  - niri: `home/.config/niri/config.kdl` ends with `include optional=true "../rice-custom/niri.kdl"` (after noctalia.kdl).
    The executor verifies the relative path resolves from `~/.config/niri/`.
  - fish: the `fish` recipe appends a final block to the generated config.fish (copy-target patch) that sources
    `~/.config/rice-custom/fish/*.fish` in sorted order, so custom aliases override Garuda's. For starship, a rice-owned
    `~/.config/fish/conf.d/rice-custom-env.fish` sets `STARSHIP_CONFIG=~/.config/rice-custom/starship.toml` only if that
    file exists (conf.d runs before config.fish's `starship init`).
  - wezterm: `wezterm.lua` calls `~/.config/rice-custom/wezterm.lua` via `pcall(dofile, …)` before `return config`. If
    the file returns a function, it is called with `config`.
  - gtk-3.0/gtk-4.0 `gtk.css`: `@import url("../rice-custom/gtk.css");` after the noctalia import. The skeleton ships an
    empty gtk.css, so the import never dangles.
  - Surgical targets (kdeglobals, dolphinrc, qt6ct.conf, kvantum.kvconfig, micro settings.json, Noctalia settings.toml)
    already keep user keys. README documents this.
- Migration: `home/.config/fish/conf.d/rice-local.fish` contains personal settings (paths, env vars) and leaves
  the repo. On this machine, the first apply moves an existing `~/.config/fish/conf.d/rice-local.fish` to
  `~/.config/rice-custom/fish/local.fish` if the target is missing. Rice does this once, records it in state, and never
  deletes user files otherwise.
- README section "Свои настройки".

### 12.3 Tasks
- **TA (Noctalia IPC):** `rice/recipe.py` (op schema), `rice/cli.py` (execute, prev, dry-run listing), recipes palette/gtk/
  starship/fonts, `tests/test_recipes_data.py`, tests with a fake `noctalia` executable on PATH (records calls, serves gets).
  Acceptance: unittest green on Noctalia 5.5; fake-noctalia test proves get-before-set (second apply makes 0 set calls),
  failure → `run_pending`, off restores `prev`; T6 acceptance still passes with `--no-run`.
- **TB (custom dir):** `mokka/custom-skel/**`, the seeding in `rice/cli.py`, hooks in `home/.config/niri/config.kdl`,
  `home/.config/wezterm/wezterm.lua`, `home/.config/gtk-{3,4}.0/gtk.css`, recipe `fish` (+ `rice-custom-env.fish` in
  `mokka/files/`), the migration, the GUI button, README. Acceptance: in temp HOME, apply seeds the dir; a user file
  `rice-custom/fish/x.fish` with `alias ls 'echo custom'` survives a second apply and `fish -c 'source config.fish; functions ls'`
  shows the custom alias; `niri validate` passes with and without `rice-custom/niri.kdl`; a bind in `rice-custom/niri.kdl`
  overriding `Mod+T` validates; `rice owned` lists nothing under `.config/rice-custom`; `install.sh` install_tree never
  touches it.
- TA and TB touch disjoint recipe files except none (fish is TB-only, palette/gtk/starship/fonts TA-only). Both edit
  `rice/cli.py`, so they run sequentially: TA, then TB.

### 12.4 Consensus fixes (Architect SOUND-WITH-CHANGES, Critic APPROVE-WITH-FIXES). These override §12.1–§12.3 where they differ
Verified live: `noctalia msg config-set <path> <TOML literal>` (strings quoted, e.g. `'"Mokka"'`) writes the **settings.toml
override layer**. Setting a key that is not yet in settings.toml **pins** it there, even if the value is unchanged.
`config-get` prints a TOML literal (parse it with `tomllib.loads("v = " + out)`). A missing key prints "error: path not found"
(rc 1). If Noctalia is not running it prints "Error: noctalia is not running" (rc 1).

TA (Noctalia op):
1. IPC writes are limited to `color_scheme` (`color-scheme-set custom <name>`) and list items in
   `theme.templates.builtin_ids` (gtk3/gtk4 for the gtk variants; a one-time `list_remove "starship"`) and
   `theme.templates.community_ids` (`starship`). There is no scalar `config-set`, so the fonts IPC step is dropped. The
   config.toml patches stay for fresh installs.
2. `noctalia msg config-reload` runs once before the first get in an apply. Get-before-set: write only if different.
3. `prev` for `theme.*` comes from **files**, not IPC: the settings.toml value if present, else the pre-patch config.toml
   value. It is read before any config.toml write in the same `_process`. List ops record `prev_present: bool`. Off reverses
   only what rice changed: remove only if absent before, re-add only if present before.
4. `disable()` executes `noctalia` off_steps as well as `run` off_steps.
5. Failure semantics: "not running" or missing binary → failure → `run_pending`. "path not found" on a list → treat as an
   empty list.
6. Keep `run noctalia msg templates-apply` in palette.
7. Tests use a fake `noctalia` on PATH that records calls and serves gets. They cover:
   - get-before-set, forced via `--force` or `run_pending=True` with the target value already present, giving 0 sets;
   - enable then disable restoring the original (settings.toml has no custom_palette → the config.toml original is
     restored, and color-scheme-set gets the original);
   - not running → `run_pending` → retry;
   - `prev_present` for lists.
   The id test checks builtin ids against builtin.toml and community ids against the stdout of
   `noctalia theme --list-templates` (or the community catalog.json). It skips when unavailable or empty, and is green
   on 5.5.
8. Starship's acceptance is manual (M3): after enabling the noctalia variant, Noctalia fetches the community template
   (network access done by Noctalia, an accepted exception) and its hook updates `[palettes.noctalia]` in
   `~/.config/starship.toml`.
9. Docs: Noctalia ops need a running Noctalia. On a fresh `install.sh` they stay `run_pending` until
   `python -m rice apply --defaults` (or GUI Apply) runs inside the session. The README and install.sh closing message
   say so.

TB (custom dir):
10. There is no migration code. Move the content of `home/.config/fish/conf.d/rice-local.fish` by hand into this
    machine's `~/.config/rice-custom/fish/local.fish` (the main thread does this) and delete the file from the repo.
11. Seed once per apply call, never on `--dry-run`. Seed only if missing. `mokka/custom-skel/` holds commented
    examples only, including separate `gtk3.css` and `gtk4.css`.
12. fish: no extra conf.d file. The appended config.fish block sets
    `STARSHIP_CONFIG=~/.config/rice-custom/starship.toml` if that file exists, then sources `rice-custom/fish/*.fish`
    sorted. The README notes that a custom starship.toml replaces the prompt.
13. wezterm: `local p = os.getenv("HOME") .. "/.config/rice-custom/wezterm.lua"`. If the file exists, call
    `wezterm.add_to_config_reload_watch_list(p)`, then `local f, err = loadfile(p)`. If `f` is set, call `f(config)`;
    otherwise call `wezterm.log_error(err)`. The user file starts with `local config = ...`.
14. gtk: both `home/.config/gtk-{3,4}.0/gtk.css` and `mokka/files/gtk-colloid.css` import
    `../rice-custom/gtk3.css` or `gtk4.css` respectively.
15. The README documents that a typo in `rice-custom/niri.kdl` breaks the whole niri config (check with `niri validate`),
    and that a symlinked `~/.config/niri` silently skips the include.
16. TB acceptance (hermetic, temp HOME, `--no-run`, no fastfetch):
    - the seed exists;
    - a user `rice-custom/fish/x.fish` survives a second apply;
    - `fish -c` sourcing config.fish shows the custom alias;
    - `niri validate` passes with and without `rice-custom/niri.kdl`, including a Mod+T override;
    - `rice owned` lists nothing under `.config/rice-custom`;
    - the dry run doesn't seed.

### 12.5 Verifier notes (PASS-WITH-NOTES), resolved
- **Dry run:** a `--dry-run` (and `status`) makes **zero** `noctalia` calls: no gets and no `config-reload`. It lists the
  op as a `run` Change only when the trigger fires. Test: the fake noctalia records 0 calls on `--dry-run`.
- **disable() with Noctalia not running:** `noctalia` off-steps run **first**. If any fails, the component stays enabled,
  nothing else is reverted, and the result is `failed` with "Noctalia not running; start it and retry". `prev` /
  `prev_present` are kept until the off-steps succeed.
- **State schema (§2 State is extended):** `prev` may also hold `"noctalia:color_scheme": [source, name]` and
  `"noctalia:list:<path>:<item>": bool` (prev_present). The palette off-step calls `color-scheme-set <source> <name>` with
  the pair read at enable time from settings.toml `[theme] source` + `custom_palette`/`builtin`. If those are absent, it
  uses the pre-patch config.toml values.
- **TA does not touch `fonts`.** TB owns `mokka/files/gtk-colloid.css` and `home/.config/gtk-{3,4}.0/gtk.css`.
- **The TB acceptance runs `python -m rice fetch fish` first** (network ok, the cache is reused), then adds:
  - `fish -c 'source $T/.config/fish/config.fish; echo $STARSHIP_CONFIG'` prints the path only when the file exists;
  - a grep for `rice-custom/gtk3.css` and `gtk4.css` in both repo gtk.css files and in gtk-colloid.css;
  - a grep for `loadfile` + `rice-custom/wezterm.lua` in wezterm.lua, and a `luac -p` / `lua -e` load check if lua is
    available;
  - grep checks in README and install.sh for the "Noctalia must be running" note and the niri typo warning.
- **Superseded:** references to `rice-local.fish` in §2 Ownership, T6, M3 and the ADR are superseded by §12.4.10. M3 now
  checks `~/.config/rice-custom/fish/local.fish`.
