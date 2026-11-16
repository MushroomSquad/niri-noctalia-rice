import json
import re
import tempfile
import unittest
from pathlib import Path

from rice import palette

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests/fixtures/upstream"
REF = ROOT / "home/.config/noctalia/palettes/Catppuccin-Lavender.json"

DARK = {
    "mPrimary": "#cba6f7", "mOnPrimary": "#1e1e2e", "mSecondary": "#b4befe", "mOnSecondary": "#181825",
    "mTertiary": "#fab387", "mOnTertiary": "#181825", "mError": "#f38ba8", "mOnError": "#181825",
    "mSurface": "#1e1e2e", "mOnSurface": "#cdd6f4", "mSurfaceVariant": "#313244",
    "mOnSurfaceVariant": "#a6adc8", "mOutline": "#585b70", "mShadow": "#11111b",
    "mHover": "#cba6f7", "mOnHover": "#11111b",
}


def shape(d):
    return {k: shape(v) if isinstance(v, dict) else None for k, v in d.items()}


def leaves(d):
    for v in d.values():
        yield from leaves(v) if isinstance(v, dict) else [v]


class PaletteTest(unittest.TestCase):
    def test_build(self):
        sources = {"garuda-mokka": FIX / "Mokka.colors",
                   "catppuccin-konsole": FIX}  # dir form
        with tempfile.TemporaryDirectory() as t:
            res = palette.build(sources, Path(t))
            self.assertEqual(set(res), {".config/noctalia/palettes/Mokka.json"})
            path = res[".config/noctalia/palettes/Mokka.json"]
            self.assertTrue(path.exists())
            got = json.loads(path.read_text())
        self.assertEqual(shape(got), shape(json.loads(REF.read_text())))
        self.assertEqual({k: v for k, v in got["dark"].items() if k != "terminal"}, DARK)
        for v in leaves(got):
            self.assertRegex(v, r"^#[0-9a-f]{6}$")
        self.assertEqual(got["dark"]["terminal"]["cursor"], "#f5e0dc")


if __name__ == "__main__":
    unittest.main()
