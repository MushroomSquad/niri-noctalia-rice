import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.environ.get("WAYLAND_DISPLAY"), "needs a Wayland session")
class GuiSmoke(unittest.TestCase):
    def test_smoke(self):
        r = subprocess.run([sys.executable, "-m", "rice", "gui", "--smoke"], cwd=ROOT, timeout=60,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
