"""라이브러리 소스가 누락·변조된 상태로 배포 자산을 만드는 것을 막는다."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import package_sources as sources


class ReleaseSourceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.payload = b"synthetic source archive"
        self.row = {
            "name": "test-library", "version": "1", "packages": {}, "covers": [],
            "archive": "example.tar.xz", "sha256": hashlib.sha256(self.payload).hexdigest(),
            "url": "https://download.qt.io/example.tar.xz",
        }

    def test_verified_cache_does_not_contact_network(self):
        target = self.cache / self.row["archive"]
        target.write_bytes(self.payload)
        with patch("urllib.request.urlopen") as network:
            self.assertEqual(sources.source_file(self.row, self.cache, download=True), target)
        network.assert_not_called()

    def test_tampered_cache_is_rejected_and_preserved(self):
        target = self.cache / self.row["archive"]
        target.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            sources.source_file(self.row, self.cache, download=True)
        self.assertEqual(target.read_bytes(), b"changed")

    def test_missing_source_and_path_escape_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            sources.source_file(self.row, self.cache, download=False)
        with self.assertRaises(ValueError):
            sources.source_file(dict(self.row, archive="../outside.tar.xz"), self.cache, download=True)

    def test_bundle_retains_verified_source_and_legal_notices(self):
        target = self.cache / self.row["archive"]
        target.write_bytes(self.payload)
        output = self.cache / "sources.zip"
        manifest = {"libraries": [self.row]}
        sources.create_bundle(manifest, [target], output)
        with zipfile.ZipFile(output) as bundle:
            self.assertEqual(bundle.read(target.name), self.payload)
            self.assertEqual(json.loads(bundle.read("corresponding-sources.json")), manifest)
            self.assertIn("DISCLAIMER.md", bundle.namelist())
            self.assertIn("docs/DEPENDENCY_SOURCES.md", bundle.namelist())
        target.write_bytes(b"changed after download")
        with self.assertRaises(ValueError):
            sources.create_bundle(manifest, [target], self.cache / "invalid.zip")
        self.assertFalse((self.cache / "invalid.zip").exists())

    def test_unlisted_bundled_qt_library_is_rejected(self):
        manifest = {"schema": 1, "qt_version": "6.test", "libraries": [self.row]}
        (self.cache / "Qt6Unknown.dll").write_bytes(b"placeholder")
        with patch("PySide6.QtCore.qVersion", return_value="6.test"):
            with self.assertRaises(ValueError):
                sources.validate_versions(manifest, self.cache)

    def test_source_manifest_matches_current_dependencies(self):
        manifest = json.loads(sources.MANIFEST.read_text(encoding="utf-8"))
        sources.validate_versions(manifest)
