"""첫 실행 실패에서도 로그·화면 정리가 이루어지고 설치 성공으로 넘어가지 않는다."""
import unittest
from unittest.mock import Mock,patch
import main


class FirstRunTest(unittest.TestCase):
    def test_extraction_exception_is_logged_and_splash_is_closed(self):
        label=Mock();app=Mock()
        with patch('src.gamedata.has_game_text',return_value=False), \
             patch('PySide6.QtWidgets.QLabel',return_value=label), \
             patch('PySide6.QtWidgets.QMessageBox.warning') as warning, \
             patch('tools.setup_data.extract',side_effect=OSError('reader failed')), \
             self.assertLogs('setup',level='ERROR') as logs:
            self.assertFalse(main.first_run_setup(app))
        label.close.assert_called_once()
        warning.assert_called_once()
        self.assertIn('reader failed','\n'.join(logs.output))

    def test_logging_is_ready_before_first_extraction(self):
        order=[]
        with patch.object(main,'QApplication'),patch('src.services.app_runtime.send_command',return_value=False), \
             patch('src.services.app_runtime.setup_logging',side_effect=lambda:order.append('log') or 'fake'), \
             patch.object(main,'install_crash_logging',side_effect=lambda *a:order.append('crash')), \
             patch.object(main,'first_run_setup',side_effect=lambda app:order.append('extract') or False), \
             patch.object(main.sys,'argv',['main.py']):
            self.assertEqual(main.main(),1)
        self.assertEqual(order,['log','crash','extract'])
