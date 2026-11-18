"""Single verify entry: original regression plus stitched contract checks."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    import stitched_checks
    stitched_checks.run(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
