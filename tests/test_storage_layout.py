"""Boundary tests for hot-tree metadata filtering."""

from pathlib import Path
import unittest

from mqelite.storage.layout import is_ignored_metadata_entry


class IgnoredMetadataEntryTests(unittest.TestCase):
    def test_only_ascii_dot_prefix_is_hidden_metadata(self) -> None:
        self.assertFalse(is_ignored_metadata_entry(Path("")))
        self.assertFalse(is_ignored_metadata_entry(Path("．finder-metadata")))
        self.assertTrue(is_ignored_metadata_entry(Path(".finder-metadata")))


if __name__ == "__main__":
    unittest.main()
