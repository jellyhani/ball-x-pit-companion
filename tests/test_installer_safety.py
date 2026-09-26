"""제거 대상과 실행 확인 실패: 실제 게임 대신 임시 폴더에서만 검증한다."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import mod_installer as mi


class InstallerSafetyTest(unittest.TestCase):
    def test_remove_only_bridge_preserves_shared_runtime_and_foreign_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = ("Balls.exe", "BepInEx/core/runtime.dll", "BepInEx/plugins/OtherMod.dll",
                    "BepInEx/config/other.cfg", "BepInEx/LogOutput.log", "dotnet/runtime.dll")
            for name in (*keep, "BepInEx/plugins/BallxPitBridge.dll"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"preserve")
            with patch.object(mi, "game_running", return_value=False):
                self.assertTrue(mi.uninstall_bridge(str(root)))
                self.assertFalse(mi.uninstall_bridge(str(root)))
            self.assertFalse((root / "BepInEx/plugins/BallxPitBridge.dll").exists())
            for name in keep:
                self.assertEqual((root / name).read_bytes(), b"preserve", name)

    def test_unknown_process_status_blocks_install_and_uninstall(self):
        with patch.object(mi.subprocess, "run", side_effect=subprocess.TimeoutExpired("tasklist", 10)):
            self.assertIsNone(mi.game_running())
            with self.assertRaises(mi.InstallError):
                mi.require_game_closed()
            with self.assertRaises(mi.InstallError):
                mi.uninstall_bridge("not-used")
            with self.assertRaises(mi.InstallError):
                mi.install(mi.ModStatus("not-used", vendor_ok=True))

    def test_nonzero_process_command_is_unknown(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="failure")
        with patch.object(mi.subprocess, "run", return_value=result):
            self.assertIsNone(mi.game_running())

    def test_running_game_blocks_removal(self):
        with patch.object(mi, "game_running", return_value=True):
            with self.assertRaises(mi.InstallError):
                mi.uninstall_bridge("not-used")

    def test_external_link_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "game"
            root.mkdir()
            (root / "Balls.exe").touch()
            outside = Path(tmp) / "outside.dll"
            outside.write_bytes(b"outside")
            target = root / "BepInEx/plugins/BallxPitBridge.dll"
            target.parent.mkdir(parents=True)
            try:
                target.symlink_to(outside)
            except OSError:
                self.skipTest("심볼릭 링크 생성 권한 없음")
            with patch.object(mi, "game_running", return_value=False):
                with self.assertRaises(mi.InstallError):
                    mi.uninstall_bridge(str(root))
            self.assertEqual(outside.read_bytes(), b"outside")
