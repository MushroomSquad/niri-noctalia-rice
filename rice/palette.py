"""Generate the Noctalia "Mokka" palette from Mokka.colors and catppuccin/konsole schemes.

Light column: Catppuccin Latte constants (https://catppuccin.com/palette, flavor "latte").
Mocha constants (Surface2, Crust) are likewise from the Catppuccin palette.
"""
import configparser
import json
from pathlib import Path

REL = ".config/noctalia/palettes/Mokka.json"
NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

# role -> (Mokka.colors section, key) or Mocha constant, and Latte constant
DARK = {
    "mPrimary": ("Selection", "BackgroundNormal"), "mOnPrimary": ("Selection", "ForegroundNormal"),
    "mSecondary": ("Window", "DecorationHover"), "mOnSecondary": ("Selection", "ForegroundInactive"),
    "mTertiary": ("Window", "ForegroundActive"), "mOnTertiary": ("Selection", "ForegroundInactive"),
    "mError": ("Window", "ForegroundNegative"), "mOnError": ("Selection", "ForegroundInactive"),
    "mSurface": ("Window", "BackgroundNormal"), "mOnSurface": ("Window", "ForegroundNormal"),
    "mSurfaceVariant": ("Button", "BackgroundNormal"), "mOnSurfaceVariant": ("Window", "ForegroundInactive"),
    "mOutline": "#585b70", "mShadow": "#11111b",
    "mHover": ("Window", "DecorationFocus"), "mOnHover": "#11111b",
}
LIGHT = {
    "mPrimary": "#8839ef", "mOnPrimary": "#eff1f5", "mSecondary": "#7287fd", "mOnSecondary": "#eff1f5",
    "mTertiary": "#fe640b", "mOnTertiary": "#eff1f5", "mError": "#d20f39", "mOnError": "#eff1f5",
    "mSurface": "#eff1f5", "mOnSurface": "#4c4f69", "mSurfaceVariant": "#ccd0da",
    "mOnSurfaceVariant": "#6c6f85", "mOutline": "#acb0be", "mShadow": "#dce0e8",
    "mHover": "#8839ef", "mOnHover": "#eff1f5",
}
# (selectionBg = Surface2, cursor = Rosewater)
TERM_EXTRA = {"dark": ("#585b70", "#f5e0dc"), "light": ("#acb0be", "#dc8a78")}


def _find(sources, name):
    """sources values may be the file itself or a directory containing it."""
    for p in sources.values():
        p = Path(p)
        if p.is_file() and p.name == name:
            return p
        if p.is_dir():
            for f in p.rglob(name):
                return f
    raise KeyError(name)


def _ini(path):
    cp = configparser.RawConfigParser(strict=False)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    return cp


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(x.strip()) for x in rgb.split(","))


def _terminal(path, flavor):
    cp = _ini(path)
    c = lambda sec: _hex(cp[sec]["Color"])
    bg, sel, cur = c("Background"), *TERM_EXTRA[flavor]
    fg = c("Foreground")
    return {
        "foreground": fg, "background": bg, "selectionFg": fg, "selectionBg": sel,
        "cursorText": bg, "cursor": cur,
        "normal": {n: c(f"Color{i}") for i, n in enumerate(NAMES)},
        "bright": {n: c(f"Color{i}Intense") for i, n in enumerate(NAMES)},
    }


def build(sources, out_dir):
    mokka = _ini(_find(sources, "Mokka.colors"))
    dark = {k: _hex(mokka[f"Colors:{v[0]}"][v[1]]) if isinstance(v, tuple) else v for k, v in DARK.items()}
    dark["terminal"] = _terminal(_find(sources, "catppuccin-mocha.colorscheme"), "dark")
    light = dict(LIGHT, terminal=_terminal(_find(sources, "catppuccin-latte.colorscheme"), "light"))
    out = Path(out_dir) / REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dark": dark, "light": light}, indent=2) + "\n")
    return {REL: out}
