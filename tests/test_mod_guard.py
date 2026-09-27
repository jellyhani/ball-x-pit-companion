"""짧은 종료 구간의 빠른 확인과 설치 중 재실행 시 기존 DLL 보존."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from src.services import mod_installer as mi
from src.services.mod_guard import ModGuard


class ModGuardRestartTest(unittest.TestCase):
    def test_pending_update_checks_faster_and_resets_after_install(self):
        app=QCoreApplication.instance() or QCoreApplication([])
        guard=ModGuard(SimpleNamespace(auto_install_mod=True),None,lambda:True)
        old=mi.ModStatus('fake',bepinex=True,enabled=True,plugin_version='1.16.0',running=True,vendor_ok=True)
        new=mi.ModStatus('fake',bepinex=True,enabled=True,plugin_version=mi.PLUGIN_VERSION,running=False,vendor_ok=True)
        try:
            with patch.object(mi,'check',return_value=old),patch.object(mi,'install',return_value=new) as install:
                guard._check(False,True)
                self.assertEqual(guard.timer.interval(),1000)
                install.assert_not_called()
                old.running=False
                guard._check(False,False)
                install.assert_called_once()
                self.assertEqual(guard.timer.interval(),10000)
        finally:guard.stop()

    def test_restart_during_staging_preserves_installed_plugin(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);plugins=root/'BepInEx/plugins';plugins.mkdir(parents=True)
            old=plugins/'BallxPitBridge.dll';old.write_bytes(b'original')
            vendor=root/'new.dll';vendor.write_bytes(b'updated')
            st=mi.ModStatus(str(root),bepinex=True,vendor_ok=True,running=False)
            with patch.object(mi,'PLUGIN_DLL',str(vendor)),patch.object(mi,'file_version',return_value='old'),\
                    patch.object(mi,'game_running',side_effect=[False,True]):
                with self.assertRaises(mi.InstallError):mi.install(st,say=lambda _:None)
            self.assertEqual(old.read_bytes(),b'original')
            self.assertEqual(list(plugins.glob('*.pending')),[])
