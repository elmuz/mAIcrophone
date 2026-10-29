from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliImportTests(unittest.TestCase):
    def test_importing_cli_does_not_load_gtk(self):
        code = (
            "import sys, maic.cli; print('gi' in sys.modules); print('maic.overlay' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        gi_loaded, overlay_loaded = result.stdout.split()
        self.assertEqual(gi_loaded, "False")
        self.assertEqual(overlay_loaded, "False")


if __name__ == "__main__":
    unittest.main()
