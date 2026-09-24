"""로그 설정과 단일 인스턴스·정상 종료 채널."""
from __future__ import annotations

import getpass
import logging
import os
from logging.handlers import RotatingFileHandler

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .settings import APP_DIR

SERVER_NAME = f"ballxpit-companion-{getpass.getuser()}"


def setup_logging() -> str:
    log_dir = os.path.join(APP_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "companion.log")
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def send_command(command: str, timeout_ms: int = 1500) -> bool:
    """실행 중인 앱에 명령을 보낸다. 앱이 없으면 False."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(command.encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.waitForReadyRead(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """현재 사용자 전용 로컬 소켓. 두 번째 실행과 stop 명령을 받는다."""

    command = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        if self._server.listen(SERVER_NAME):
            return True
        # 이전 실행이 비정상 종료되어 이름만 남은 경우
        QLocalServer.removeServer(SERVER_NAME)
        return self._server.listen(SERVER_NAME)

    def _on_connection(self):
        sock = self._server.nextPendingConnection()
        if sock is None:
            return

        def read():
            data = bytes(sock.readAll()).decode("utf-8", errors="replace").strip()
            if data:
                sock.write(b"ok\n")
                sock.flush()
                self.command.emit(data[:32])
        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)

    def close(self):
        self._server.close()
