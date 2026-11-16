# SPEC-2: Packages vs appearance split + GUI rework

Status: draft v1 (2026-11-16). Input for PLAN-2 → architect/critic consensus → verifier → implementation.
Builds on SPEC.md / PLAN.md §1–§12 (implemented, on `main`).

## 1. Problem

- Rice recipes mix two different things: **installing software** (sudo, pacman/AUR, terminal; greeter, Handy,
  FireDragon, base packages) and **appearance** (config files, Noctalia settings; no sudo). They share one on/off list in
  the GUI, which confuses the user.
- Package lists are duplicated: `install.sh` has `REPO_PKGS`/`AUR_PKGS`, and recipes carry `packages = {...}`.
- rice can't tell that software is already installed when the old install.sh installed it: greeter, Handy and base
  packages show "off" even when present. FireDragon shows "missing firedragon-catppuccin-bin" although `firedragon` is
  installed.
- The GUI is hard to use:
  - a narrow column with huge empty space and tiny previews;
  - the palette preview and swatch overlap;
  - technical group ids;
  - a "needs package X" badge even when X is installed;
  - no per-component state (applied / needs apply / hand-edited / waiting for Noctalia);
  - no pending-changes count;
  - no notice that app installs open a terminal and use sudo.

## 2. Goals

1. **Packages layer.** One manifest, `mokka/packages.toml`, is the single source of package lists for both install.sh
   and the GUI. Entries are **package sets** with:
   - `id`, `title`, `description`, `group` (base, desktop, apps, dev, …), `default`;
   - `repo = [...]`, `aur = [...]`;
   - optional `any_of` alternatives (e.g. `firedragon` | `firedragon-catppuccin-bin` both satisfy FireDragon);
   - an optional **install step** (`install.sh --only <step>` for greeter/Handy, which do more than install packages);
   - optional `post`/`remove` actions (e.g. remove firefox after FireDragon);
   - a **detection** rule (`installed_if`): all packages present via `pacman -Q`, or a command, a file, or
     `systemctl is-enabled <unit>`.

   State always comes from the live system, never from rice's state file, so things the old install.sh put on the
   machine show as installed.
2. **Appearance layer.** Rice recipes keep only config/settings steps. A recipe declares `needs = [<package-set id>…]`
   instead of `packages`. Missing needs never install silently.
   - In the GUI the card shows "Needs: kvantum, qt6ct-kde" and an **Install** button. It installs the missing sets via
     the packages layer (one terminal, one sudo), then the component can be applied.
   - In the CLI, `apply` skips a component with unmet needs, reports `needs <ids>`, and exits non-zero only if a
     requested id was skipped.
   - `python -m rice packages install <ids>` installs sets. `apply --install-needs` is an explicit opt-in that installs
     needs first; `install.sh` uses it.
3. **install.sh** has two clear phases:
   1. **packages**: base sets plus the sets the user chose, read from the manifest; the cachy-update/paru bootstrap and
      `--needed` installs stay.
   2. **appearance**: `rice apply --defaults`.

   `--only <set>` stays for greeter/Handy, driven by the manifest. No package list lives in install.sh any more.
4. **GUI rework (English).**
   - Two top-level tabs: **Packages** and **Appearance**.
   - Cards in a responsive grid (2–3 columns) across the full width. Large previews open fullscreen on click.
   - The palette swatch is a proper color row under the preview.
   - Groups have human titles and icons.
   - Each card has a compact state line:
     - Packages: Installed / Not installed / Partially installed (lists missing).
     - Appearance: Applied ✓ / Needs apply / Hand-edited / Waiting for Noctalia / Needs packages.
   - Variants are a dropdown in the card.
   - The header shows a summary ("N pending · upstream updates: yes/no") and **Apply (N)** with a count.
   - Install actions are labeled "opens a terminal, needs sudo".
   - Badges are accurate: "needs package X" shows only when X is missing.
   - Existing actions keep working: check updates, fetch, show changes (diff dialog), open custom folder.
5. **Cleanups found during M-checks:** drop the `yakuake` component (KDE/X11 drop-down terminal, irrelevant on niri) or
   keep it hidden when not installed; the default is to drop it.

## 3. Non-goals

- Uninstalling base packages from the GUI. Removing sets is allowed only for optional app sets, behind a confirmation.
- Localisation (the GUI is English only).
- Changing the rice engine semantics from PLAN §2/§12: ownership, run_pending, noctalia op, custom dir.

## 4. Constraints

- Stdlib + PyGObject/GTK4/libadwaita only. Minimal code, and the existing tests stay green.
- Package detection is fast and offline (`pacman -Qq` once per refresh, not per package).
- No `-Syu`. A stale package DB (404 on download) is detected and reported as "run cachy-update / sudo pacman -Syu
  first".
- Sudo only inside a terminal the user sees (wezterm), never hidden in the GUI process.
- `install.sh --only` never calls rice (no recursion), as before.

## 5. Open questions (for consensus)

1. Manifest format: a single `mokka/packages.toml` with `[[set]]` tables, or one file per set? Default: one file.
2. Should base sets (niri, noctalia, wezterm, fish …) appear in the GUI as read-only "Installed" cards, or be hidden?
   Default: shown, collapsed under "Base", no uninstall.
3. Do `needs` entries reference package-set ids or raw package names? Default: set ids, with small sets for theme
   packages (kvantum+qt6ct-kde, catppuccin-cursors-mocha, inter-font, sassc …).
4. How does the GUI run the terminal for installs: `wezterm start -- install.sh --only …` / `rice packages install`, and
   how does it learn the result (exit code file)? Default: a wrapper writes the exit code to a temp file; the GUI polls
   it and refreshes.
