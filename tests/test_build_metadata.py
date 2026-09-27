"""GitHub 소스 ZIP과 Git 도구가 없는 환경에서도 빌드 식별자가 거짓 상태가 되지 않는다."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.build_metadata import source_identity


class BuildMetadataTest(unittest.TestCase):
    def test_source_archive_does_not_use_an_unrelated_parent_repository(self):
        with tempfile.TemporaryDirectory() as folder, patch("subprocess.check_output") as git:
            self.assertEqual(source_identity(Path(folder)), {"revision": "source-archive", "dirty": None})
        git.assert_not_called()

    def test_missing_git_does_not_prevent_personal_build(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".git").mkdir()
            with patch("subprocess.check_output", side_effect=FileNotFoundError):
                self.assertEqual(source_identity(root), {"revision": "unavailable", "dirty": None})

    def test_repository_errors_are_not_reported_as_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".git").write_text("gitdir: missing")
            with patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(128, ["git"])):
                self.assertIsNone(source_identity(root)["dirty"])

    def test_repository_revision_and_changes_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".git").mkdir()
            with patch("subprocess.check_output", side_effect=["abc123\n", " M README.md\n"]):
                self.assertEqual(source_identity(root), {"revision": "abc123", "dirty": True})
