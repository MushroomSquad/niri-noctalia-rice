#!/usr/bin/env bash
# Standalone installer: niri + Noctalia (Catppuccin Lavender), wezterm, fish+starship, greetd/ReGreet,
# wallpapers, Handy (speech-to-text). Arch/CachyOS only. Run as a normal user; sudo is asked when needed.
# Optional parts are asked interactively; without a TTY or with --yes the defaults [Y/n] are used.
#   ./install.sh [--only pkgs,greeter,handy] [--no-mokka] [--gui] [--yes] [--no-pkgs] [--no-greeter] [--no-handy] [--no-wallpapers] [--no-nvim] [--no-agents] [--no-firedragon] [--fish-theme]
# Re-run anytime to update: ./install.sh update   (= --yes; pulls the repo first; package updates run through cachy-update, interactively)
# --firedragon is accepted for compatibility (FireDragon is installed by default).
# shellcheck disable=SC2034 # UPDATE is informational (update == --yes)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $EUID -ne 0 ]] || { echo "run as normal user, not root"; exit 1; }
command -v pacman >/dev/null || { echo "pacman not found: Arch-based only"; exit 1; }

# Always available to both the full installer and --only greeter.
outs=$(for s in /sys/class/drm/card*-*/status; do
  [[ $(cat "$s" 2>/dev/null) == connected ]] && { n=${s%/status}; echo "${n#*/card*-}"; }; done; true)
CFG="$HOME/.config"
YES=0; [[ -t 0 ]] || YES=1
ONLY=""; GUI=0; UPDATE=0
RICE_WITH=()
declare -A OPT=()   # name -> 1/0, preset by flags
while (( $# )); do
  case $1 in
    update) UPDATE=1; YES=1;;
    --yes) YES=1;;
    --only)
      [[ $# -ge 2 && -n $2 && $2 != --* ]] || { echo "--only requires pkgs,greeter,handy (comma-separated)"; exit 1; }
      ONLY=$2; shift;;
    --no-pkgs) OPT[pkgs]=0;; --no-greeter) OPT[greeter]=0;; --no-handy) OPT[handy]=0;;
    --no-wallpapers) OPT[wallpapers]=0;; --no-mokka) OPT[mokka]=0;; --gui) GUI=1;;
    --nvim) OPT[nvim]=1; RICE_WITH+=(nvim);; --no-nvim) OPT[nvim]=0;;
    --agents) OPT[agents]=1; RICE_WITH+=(agents);; --no-agents) OPT[agents]=0;;
    --firedragon) OPT[firedragon]=1;; --no-firedragon) OPT[firedragon]=0;;
    --fish-theme) OPT[fishtheme]=1;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
  shift
done
if [[ -n $ONLY ]]; then
  [[ $ONLY != ,* && $ONLY != *, && $ONLY != *,,* ]] || { echo "invalid --only list: $ONLY"; exit 1; }
  for name in pkgs greeter handy wallpapers nvim agents firedragon fishtheme mokka; do OPT[$name]=0; done
  IFS=, read -ra steps <<< "$ONLY"
  for name in "${steps[@]}"; do
    case $name in
      pkgs|greeter|handy) OPT[$name]=1;;
      *) echo "unknown --only step: $name (expected pkgs,greeter,handy)"; exit 1;;
    esac
  done
fi

# Pull the latest repo before doing anything, then re-exec once so the rest of this run uses the fresh copy.
# NIRI_RICE_REEXECED guards against looping. Skipped for --only: rice recipes call install.sh --only mid-apply.
if [[ -z $ONLY && -z ${NIRI_RICE_REEXECED:-} ]] && git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git -C "$SRC" pull --ff-only; then
    NIRI_RICE_REEXECED=1 exec "$SRC/install.sh" "$@"
  else
    echo "!! git pull failed (local changes in $SRC?); continuing with the checked-out copy"
  fi
fi

# ask <name> <question> <default y|n>  -> sets OPT[name]
ask() {
  [[ -n ${OPT[$1]:-} ]] && return
  local def=$3 ans
  if (( YES )); then ans=$def; else read -rp "$2 [$( [[ $def == y ]] && echo Y/n || echo y/N )] " ans; ans=${ans:-$def}; fi
  [[ $ans =~ ^[Yy] ]] && OPT[$1]=1 || OPT[$1]=0
}

if [[ -z $ONLY ]]; then
ask pkgs       "Install packages (pacman/AUR)?" y
ask greeter    "Replace the display manager with greetd + ReGreet (Catppuccin login screen)?" y
ask wallpapers "Copy wallpapers to ~/Pictures/Wallpapers?" y
ask handy      "Download and set up Handy (speech-to-text, Ctrl+Space)?" y
ask nvim       "Install Neovim + AstroNvim config (plugins are fetched on first nvim start)?" y
ask agents     "Install Claude Code and Codex agents (GSD) to ~/.claude/agents and ~/.codex/agents?" y
ask firedragon "Install FireDragon (firedragon-catppuccin-bin) replacing Firefox?" y
ask fishtheme  "Apply Catppuccin Mocha colors to fish syntax highlighting?" n
ask mokka      "Apply Garuda Mokka components with rice?" y
fi

REPO_PKGS=(python git niri noctalia cachyos-niri-noctalia wezterm fish fisher starship eza bat fastfetch ugrep direnv
  adw-gtk-theme inter-font catppuccin-gtk-theme-mocha ttf-jetbrains-mono-nerd adwaita-fonts
  noto-fonts noto-fonts-emoji xwayland-satellite xdg-desktop-portal-gnome
  xdg-desktop-portal-gtk gnome-keyring wl-clipboard curl
  nodejs npm base-devel telegram-desktop filezilla openssh k9s podman podman-compose slirp4netns fuse-overlayfs)
(( OPT[greeter] )) && REPO_PKGS+=(greetd greetd-regreet plymouth)
(( OPT[handy] ))   && REPO_PKGS+=(fuse2 wtype xdotool)
AUR_PKGS=(tela-circle-icon-theme-dracula clash-verge-rev-bin tg-ws-proxy-bin spotify-launcher)

if (( OPT[pkgs] )); then
  # Updates go through cachy-update (never -Syu); everything below only fills in what is missing.
  sudo pacman -S --needed --noconfirm cachy-update libnotify
  if [[ -t 0 ]]; then
    echo ">>> Launching cachy-update: go through its prompts, this script continues after it exits."
    cachy-update || echo "!! cachy-update exited non-zero; continuing anyway"
  else
    echo "!! no TTY: skipping the interactive cachy-update run, run it yourself afterwards"
  fi

  # existing but non-functional (e.g. a paru-bin linked against the wrong libalpm) doesn't count
  helper=""
  for h in paru yay; do command -v "$h" >/dev/null && "$h" --version >/dev/null 2>&1 && { helper=$h; break; }; done
  if [[ -z $helper ]]; then
    # built from source (not paru-bin) so it links against this box's libalpm
    sudo pacman -Rns --noconfirm paru-bin 2>/dev/null || true
    tmp=$(mktemp -d)
    git clone --depth 1 https://aur.archlinux.org/paru.git "$tmp" \
      && (cd "$tmp" && makepkg -si --needed --noconfirm) || echo "!! paru bootstrap failed"
    rm -rf "$tmp"
    command -v paru >/dev/null && paru --version >/dev/null 2>&1 && helper=paru
  fi

  sudo pacman -S --needed --noconfirm "${REPO_PKGS[@]}" || {
    # names differ between repos; retry one by one so a single miss doesn't abort
    for p in "${REPO_PKGS[@]}"; do sudo pacman -S --needed --noconfirm "$p" || echo "!! skipped $p"; done; }
  if [[ -n $helper ]]; then "$helper" -S --needed --noconfirm "${AUR_PKGS[@]}" || echo "!! AUR failed: ${AUR_PKGS[*]}"
  else echo "!! no AUR helper: install manually: ${AUR_PKGS[*]}"; fi
  # rootless podman needs a subuid/subgid range; useradd grants one by default, but add it if missing
  grep -q "^$USER:" /etc/subuid 2>/dev/null || sudo usermod --add-subuids 100000-165535 "$USER"
  grep -q "^$USER:" /etc/subgid 2>/dev/null || sudo usermod --add-subgids 100000-165535 "$USER"
fi

if [[ -z $ONLY ]]; then
# ---------- user files (existing ones are backed up to <file>.bak-<ts>) ----------
# Read once; before the first apply this output is empty.
declare -A OWNED=()
owned_paths=$(cd "$SRC" && python -m rice owned)
while IFS= read -r rel; do
  if [[ -n $rel ]]; then OWNED["$rel"]=1; fi
done <<< "$owned_paths"
TS=$(date +%Y%m%d%H%M%S)
install_tree() { # install_tree <srcdir> <dstdir>
  local f d rel
  while IFS= read -r -d '' f; do
    d="$2/${f#./}"
    rel=${d#"$HOME/"}
    if [[ -n ${OWNED[$rel]:-} ]]; then
      echo "kept (owned by rice): $rel"
      continue
    fi
    mkdir -p "$(dirname "$d")"
    [[ -e $d ]] && ! cmp -s "$1/$f" "$d" && cp -a "$d" "$d.bak-$TS"
    cp "$1/$f" "$d"
  done < <(cd "$1" && find . -type f -print0)
}
install_tree "$SRC/home" "$HOME"
(cd "$SRC" && python -c 'from pathlib import Path; from rice.cli import _seed_custom; _seed_custom(Path.home())')
sed -i "s|@HOME@|$HOME|g" "$CFG/noctalia/config.toml"
if (( OPT[wallpapers] == 0 )); then
  # config points at wallpapers we didn't copy: switch Noctalia's wallpaper off instead of leaving a dead path
  sed -i '/^\[wallpaper\]/,/^enabled/ s/^enabled = true/enabled = false/' "$CFG/noctalia/config.toml"
else
  install_tree "$SRC/wallpapers" "$HOME/Pictures/Wallpapers"
fi

# monitors: generated from what niri sees (now if a session is running, otherwise at first niri start)
"$HOME/.local/bin/niri-autodisplay" 2>/dev/null || true

# Noctalia notifications are pinned to DP-2: drop the pin if that output doesn't exist here
grep -qx 'DP-2' <<<"$outs" || sed -i '/^monitors = \[ "DP-2" \]/d' "$CFG/noctalia/config.toml"

if command -v gsettings >/dev/null; then
  gsettings set org.gnome.desktop.interface icon-theme 'Tela-circle-dracula' || true
  gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark' || true
fi

# tg-ws-proxy: autostart (in its tray) only if the AUR install succeeded
mkdir -p "$CFG/niri/cfg"
if command -v tg-ws-proxy >/dev/null; then
  grep -qF 'tg-ws-proxy' "$CFG/niri/cfg/autostart.kdl" 2>/dev/null ||
    printf '    spawn-sh-at-startup "tg-ws-proxy"\n' >> "$CFG/niri/cfg/autostart.kdl"
fi

# cachy-update tray applet + daily check (it has no non-interactive update flag, so not used for `update`)
if command -v cachy-update >/dev/null; then
  cachy-update --tray --enable || true
  cachy-update --check --enable || true
fi

if command -v npm >/dev/null; then
  # user-owned prefix, no sudo: `sudo npm install -g` skips postinstall scripts as root
  npm config get prefix | grep -qx "$HOME/.local" || npm config set prefix "$HOME/.local"
  for pkgcmd in "@anthropic-ai/claude-code:claude" "@openai/codex:codex"; do
    pkg=${pkgcmd%%:*}; cmd=${pkgcmd##*:}
    working() { command -v "$cmd" >/dev/null && "$cmd" --version >/dev/null 2>&1; }
    working && continue
    npm install -g "$pkg" || true
    # postinstall can race its sibling optionalDependency on a fresh install; a second run fixes it
    working || npm install -g "$pkg" || true
    working || echo "!! $cmd still broken after a retry: run 'node \"\$(npm root -g)/$pkg/install.cjs\"' by hand"
  done
fi

# initrd root mount timeout (dracut/systemd initrd only): CachyOS' 15s DefaultTimeoutStartSec also applies to
# mounting / in the initrd; a slow btrfs mount after an unclean shutdown lands in emergency mode. Give it 120s.
if command -v dracut >/dev/null && ! cmp -s "$SRC/etc/systemd/sysroot.mount.d/10-timeout.conf" /etc/systemd/system/sysroot.mount.d/10-timeout.conf; then
  sudo install -Dm644 "$SRC/etc/systemd/sysroot.mount.d/10-timeout.conf" /etc/systemd/system/sysroot.mount.d/10-timeout.conf
  sudo install -Dm644 "$SRC/etc/dracut.conf.d/10-sysroot-timeout.conf" /etc/dracut.conf.d/10-sysroot-timeout.conf
  if command -v limine-update >/dev/null; then sudo limine-update; else sudo dracut --regenerate-all --force; fi \
    || echo "!! initramfs rebuild failed: the root mount timeout applies after the next successful rebuild"
fi

if command -v fish >/dev/null; then
  fish -c 'type -q fisher; and fisher update' || true
  (( OPT[fishtheme] )) && { fish -c 'fish_config theme save catppuccin-mocha' || true; }
  [[ $SHELL == */fish ]] || chsh -s "$(command -v fish)" || true
fi

fi # full installer user settings

# ---------- Handy: AppImage from GitHub releases + settings ----------
if (( OPT[handy] )); then
  H="$HOME/.local/opt/handy"; mkdir -p "$H"
  ver=${HANDY_VERSION:-$(curl -fsSL https://api.github.com/repos/cjpais/Handy/releases/latest | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -1 || true)}
  if [[ -n $ver ]] && curl -fL --retry 3 -o "$H/Handy.AppImage.part" \
       "https://github.com/cjpais/Handy/releases/download/v$ver/Handy_${ver}_amd64.AppImage"; then
    mv "$H/Handy.AppImage.part" "$H/Handy.AppImage"; chmod +x "$H/Handy.AppImage"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$H/Handy.AppImage" "$HOME/.local/bin/handy"
    echo "Handy $ver installed"
    # settings only on first install, so an already-configured Handy isn't overwritten
    S="$HOME/.local/share/com.pais.handy/settings_store.json"
    if [[ ! -e $S ]]; then mkdir -p "$(dirname "$S")"; sed "s|@HOME@|$HOME|g" "$SRC/handy/settings_store.json" > "$S"; fi
    mkdir -p "$CFG/niri/cfg"
    # The shell launched by niri expands $HOME when running this command.
    # shellcheck disable=SC2016
    grep -qF '.local/bin/handy' "$CFG/niri/cfg/autostart.kdl" 2>/dev/null ||
      printf '    spawn-sh-at-startup "$HOME/.local/bin/handy"\n' >> "$CFG/niri/cfg/autostart.kdl"
  else
    echo "!! Handy download failed (version '${ver:-?}'); rerun with HANDY_VERSION=x.y.z or --no-handy"
    OPT[handy]=0
  fi
fi
# no Handy -> drop its keybinds so Ctrl+Space isn't swallowed by a dead binding
if [[ -z $ONLY || ,$ONLY, == *,handy,* ]] && (( OPT[handy] == 0 )) && [[ -f $CFG/niri/cfg/keybinds.kdl ]]; then
  sed -i '/Handy/d' "$CFG/niri/cfg/keybinds.kdl"
fi

# ---------- greeter (greetd + ReGreet in a mini niri session) ----------
if (( OPT[greeter] )); then
  sudo install -Dm644 "$SRC/etc/greetd/config.toml"  /etc/greetd/config.toml
  sudo install -Dm644 "$SRC/etc/greetd/regreet.toml" /etc/greetd/regreet.toml
  sudo install -Dm644 "$SRC/etc/greetd/greet.jpg"    /usr/share/backgrounds/greet.jpg
  tmp=$(mktemp); first=""
  { echo '// generated by install.sh: greeter niri session'
    while IFS= read -r o; do
      [[ $o =~ ^(DP|HDMI|eDP|DVI|VGA)- ]] || continue
      [[ -z $first ]] && first=$o
      echo "output \"$o\" {"; [[ $o == "$first" ]] && echo '  focus-at-startup'; echo '  backdrop-color "#1e1e2e"'; echo '}'
    done <<< "$outs"
    echo "window-rule {"; echo "  match at-startup=true"; echo "  open-on-output \"${first:-DP-1}\""; echo "}"
    sed -n '/^spawn-sh-at-startup/,$p' "$SRC/etc/greetd/niri.kdl"
  } > "$tmp"
  sudo install -Dm644 "$tmp" /etc/greetd/niri.kdl; rm -f "$tmp"
  sudo systemctl disable sddm gdm lightdm ly 2>/dev/null || true
  sudo systemctl enable -f greetd.service
fi

# Recipe callbacks must never enter rice (including the read-only owned command).
[[ -z $ONLY ]] || exit 0

# The launcher needs the checkout as its working directory for python -m rice.
launcher="${XDG_DATA_HOME:-$HOME/.local/share}/applications/niri-noctalia-rice.desktop"
mkdir -p "$(dirname "$launcher")"
repo_escaped=$(printf '%s' "$SRC" | sed 's/[\\&|]/\\&/g')
sed "s|@REPO@|$repo_escaped|g" "$SRC/mokka/niri-noctalia-rice.desktop" > "$launcher"
chmod 644 "$launcher"

if (( OPT[mokka] )); then
  if (( GUI )); then
    (cd "$SRC" && python -m rice gui)
  else
    rice_args=(apply --defaults --yes)
    (( OPT[pkgs] )) || rice_args+=(--no-packages)
    with=(); without=()
    for name in nvim agents; do
      (( OPT[$name] )) || without+=("$name")
    done
    for name in "${RICE_WITH[@]}"; do
      (( OPT[$name] == 0 )) || with+=("$name")
    done
    if (( OPT[firedragon] )); then with+=(firedragon); else without+=(firedragon); fi
    (( ${#with[@]} == 0 )) || rice_args+=(--with "$(IFS=,; echo "${with[*]}")")
    (( ${#without[@]} == 0 )) || rice_args+=(--without "$(IFS=,; echo "${without[*]}")")
    [[ ${RICE_NO_RUN:-0} != 1 ]] || rice_args+=(--no-run)
    (cd "$SRC" && python -m rice "${rice_args[@]}")
  fi
fi

# FireDragon replaces Firefox: drop Firefox only once FireDragon is actually installed
if (( OPT[firedragon] )) && command -v firedragon >/dev/null; then
  sudo pacman -Rns --noconfirm firefox 2>/dev/null || true
  # point Mod+B at FireDragon so the binding doesn't go dead
  [[ ! -f $CFG/niri/cfg/keybinds.kdl ]] ||
    sed -i 's/Open Browser: firefox" { spawn "firefox"/Open Browser: firedragon" { spawn "firedragon"/' "$CFG/niri/cfg/keybinds.kdl"
fi

cat <<'MSG'

Done. Log out/reboot (initramfs/greeter changes need it), then inside niri run once:
  python -m rice apply --defaults # run from the repository, or use GUI Apply
Noctalia must be running: its ops stay pending until Apply runs inside the session.
  noctalia msg templates-apply     # regenerates wezterm/gtk/starship/niri colors from the palette
Backups of overwritten files: *.bak-<timestamp>
OS updates: cachy-update (tray applet + daily check enabled); ./install.sh update re-pulls this repo.
Handy: pick/download a speech model in its window on first start (models are not in the repo, ~1.8 GB for mine).
MSG
