import unittest
from pathlib import Path

from rice import cli

ROOT = Path(__file__).resolve().parent.parent


class Previews(unittest.TestCase):
    def test_previews_exist(self):
        for c in cli.components():
            if list((ROOT / "mokka/previews" / c.id).glob("*.png")):
                continue
            self.assertTrue(c.previews, f"{c.id}: no committed preview and no previews entry")
            found = skipped = 0
            for ref in c.previews:  # "<lock-id>:<path>"
                lid, _, rel = ref.partition(":")
                base = cli.source_path(lid)
                if base is None:
                    skipped += 1
                elif (base / rel).is_file():
                    found += 1
                else:
                    self.fail(f"{c.id}: {ref} missing in fetched source")
            if not skipped:  # sources not fetched: stay offline
                self.assertTrue(found, f"{c.id}: no preview file")


if __name__ == "__main__":
    unittest.main()
