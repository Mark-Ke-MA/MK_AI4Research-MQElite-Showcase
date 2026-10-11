"""Run the selected synthetic tests offline."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("此展示需要 Python 3.11 或更新版本；请显式选择合适的 Python 解释器。")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    tests = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
