"""BALL x PIT 선택 도우미 실행 진입점.

    pythonw main.py          실행 (이미 실행 중이면 그 창을 연다)
    python  main.py --stop   실행 중인 도우미에 정상 종료 요청
    python  main.py --ensure-mod   게임 연동 모드 확인·자동 설치 (run_overlay.bat 이 먼저 부른다)
"""
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.i18n import tr


def install_crash_logging(log: logging.Logger, log_path: str):
    """예외를 로그에 남기고 앱은 계속 실행한다.

    PySide6 는 시그널 처리 중 잡히지 않은 예외가 나면 기본 excepthook 일 때 프로그램을 강제 종료한다.
    pythonw 로 실행하면 콘솔이 없어 흔적 없이 꺼지므로, 직접 훅을 달아 기록한다.
    """
    import faulthandler
    import os
    import threading

    def hook(exc_type, exc, tb):
        log.error("처리되지 않은 예외", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda a: log.error("스레드 예외 (%s)", a.thread, exc_info=(a.exc_type, a.exc_value,
                                                                                      a.exc_traceback))
    crash = open(os.path.join(os.path.dirname(log_path), "native_crash.log"), "a", encoding="utf-8")
    faulthandler.enable(file=crash, all_threads=True)   # 네이티브 충돌 시 스택 기록


def ensure_mod() -> int:
    """게임 연동 모드 확인. 없거나 옛 버전이면 설치한다 (게임이 켜져 있으면 도우미가 종료를 기다렸다 설치)."""
    from src.services import mod_installer as mi
    st = mi.check()
    print(f"게임 연동: {st.summary}")
    if st.game_dir:
        print(f"  게임 폴더: {st.game_dir} (빌드 {st.build_id or '?'})")
    if st.ok or not st.needs_install:
        if st.enabled is False:
            print("  BepInEx 를 다시 켜려면: tools\\bepinex\\bridge.ps1 enable")
        return 0
    if st.running:
        print("  게임이 실행 중이라 지금은 설치할 수 없습니다. 도우미가 게임 종료를 기다렸다가 설치합니다.")
        return 0
    try:
        new = mi.install(st, say=lambda m: print("  " + m))
        print(f"  완료: {new.summary}")
        return 0
    except mi.InstallError as e:
        print(f"  설치 실패: {e}")
        return 1


def first_run_setup(app) -> bool:
    """게임 자료가 없으면(첫 실행·게임 업데이트 뒤 삭제 등) 사용자의 게임 파일에서 추출한다. 실패하면 안내 후 False."""
    from src import gamedata as gd
    if gd.has_game_text():
        return True
    from PySide6.QtWidgets import QLabel, QMessageBox
    splash = QLabel(tr("게임 자료를 준비하고 있습니다…"))
    splash.setStyleSheet("padding: 18px; font-size: 14px;")
    splash.show()
    app.processEvents()
    from tools.setup_data import extract
    try:
        ok, msg = extract(say=lambda m: logging.getLogger('setup').info('%s',m))
    except Exception:
        logging.getLogger('setup').exception('첫 실행 게임 자료 준비 실패')
        ok,msg=False,tr("게임 자료 준비 중 오류가 발생했습니다. 진단 로그를 확인해 주세요.")
    finally:
        splash.close()
    if not ok:
        QMessageBox.warning(None,tr("BALL x PIT 도우미"),msg)
    return ok


def main() -> int:
    if "--ensure-mod" in sys.argv:
        return ensure_mod()
    if "--setup-data" in sys.argv:
        from tools.setup_data import extract
        ok, msg = extract()
        print(msg)
        return 0 if ok else 1
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(tr("BALL x PIT 도우미"))
    app.setQuitOnLastWindowClosed(False)

    from src.services.app_runtime import InstanceServer, send_command, setup_logging

    if "--stop" in sys.argv:
        ok = send_command("quit")
        print("종료 요청을 보냈습니다." if ok else "실행 중인 도우미가 없습니다.")
        return 0 if ok else 1
    if send_command("show"):
        return 0   # 이미 실행 중: 기존 인스턴스가 창을 연다

    log_path = setup_logging()
    log = logging.getLogger("main")
    install_crash_logging(log, log_path)
    from src.version import build_info
    log.info('도우미 빌드: %s',build_info())
    if not first_run_setup(app):
        return 1
    server = InstanceServer()
    if not server.listen():
        log.error("단일 실행 채널을 열지 못했습니다")

    from src.app_controller import AppController
    try:
        controller = AppController(app)
    except FileNotFoundError as e:
        log.exception("데이터 파일 없음")
        print(f"데이터 파일을 찾지 못했습니다: {e}")
        return 1

    def on_command(cmd: str):
        if cmd == "quit":
            log.info("종료 요청 받음")
            app.quit()
        elif cmd == "show":
            controller.show_control()

    server.command.connect(on_command)
    app.aboutToQuit.connect(controller.shutdown)
    app.aboutToQuit.connect(server.close)
    controller.start(show="--tray" not in sys.argv)
    log.info("도우미 시작 (로그: %s)", log_path)
    return app.exec()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()     # exe: 계산 프로세스(ProcessPoolExecutor)가 앱을 다시 켜지 않게
    sys.exit(main())
