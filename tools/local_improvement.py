r"""로컬 감시 → codex queue → 회차 완료 확인. 중복 요청을 쌓지 않는다.

start --thread UUID / status / stop / ack REQUEST_ID --outcome continue|wait|stop
감시만 로컬에서 실행한다. 실제 AI 검토에는 Codex 연결과 사용량이 필요하다.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.improvement_audit import inspect, digest

FOLDER = ROOT / "build/improvement/local"


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def read_state(folder=FOLDER):
    path = folder / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state, folder=FOLDER):
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "state.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(folder / "state.json")


def due(state, evidence, now):
    return (state.get("enabled", False) and not state.get("pending")
            and now >= state.get("next_at", 0)
            and (state.get("mode") == "continue" or evidence != state.get("baseline")))


def acknowledge(state, request, outcome, now):
    if (state.get("pending") or {}).get("id") != request:
        raise ValueError("현재 대기 요청과 완료 번호가 다릅니다.")
    state.update(pending=None, mode=outcome, last_ack=now,
                 next_at=now + state["interval"])
    if outcome == "stop":
        state["enabled"] = False


def prompt(request):
    return (
        f"[로컬 개선 루프 {request}] 사용자가 요청한 지속 코드 검토·디버깅의 다음 회차입니다. "
        "IMPROVEMENT_LOOP.md와 build/improvement/status.md를 읽고 새 기록과 미검토 코드를 확인하세요. "
        "가장 중요한 문제 하나를 재현·수정·검증하고 상태를 갱신하세요. 게임 조작·강제 종료·공개 업로드 금지, "
        "다른 작업의 변경 보존, 사용자에게 종료 보고 반복 요구 금지. 이미 확인한 같은 문제의 검사를 반복하지 마세요. "
        "의미 있는 결과만 알려 주세요. 이번 회차를 마칠 때 다음 명령을 한 번 실행하세요: "
        f".venv\\Scripts\\python.exe tools\\local_improvement.py ack {request} --outcome wait . "
        "즉시 이어서 조사할 구체적 미완료 항목이 있으면 wait 대신 continue, 사용자 중지 요청이면 stop을 사용하세요. "
        "wait는 새 증거가 생길 때까지 AI 재호출을 멈추며, continue도 설정된 대기 간격을 지킵니다."
    )


def deliver(state, evidence, sender, now):
    """전송 전에 요청 번호를 확보한다. 실패/시간 초과 때 재전송하지 않아 중복을 막는다."""
    request = uuid.uuid4().hex[:12]
    state.update(pending={"id": request, "at": now, "status": "sending"}, baseline=evidence)
    save_state(state)
    try:
        result = sender([state["codex"], "queue", "--thread", state["thread"], "--message", prompt(request)],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-1500:])
        state["pending"]["status"] = "queued"
        state["error"] = None
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        state["pending"]["status"] = "unconfirmed"
        state.update(enabled=False, error=str(exc))
    save_state(state)


def run():
    with locked(FOLDER / "runner.lock"):
        while True:
            try:
                with locked(FOLDER / "state.lock"):
                    state = read_state()
                    if not state.get("enabled"):
                        return
                    state.update(pid=os.getpid(), heartbeat=time.time())
                    if not state.get("pending") and time.time() >= state.get("next_at", 0):
                        profile = Path(os.environ["LOCALAPPDATA"]) / "BallxPitCompanion"
                        report = inspect(profile)
                        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                                  text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                        changes = subprocess.run(["git", "diff", "--no-ext-diff"], cwd=ROOT, capture_output=True,
                                                 timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                        if revision.returncode or changes.returncode:
                            raise OSError("저장소 변경 상태를 확인하지 못했습니다.")
                        evidence = digest([report["evidence"], revision.stdout.strip(),
                                           digest(changes.stdout.hex())])
                        if due(state, evidence, time.time()):
                            deliver(state, evidence, subprocess.run, time.time())
                        else:
                            state["next_at"] = time.time() + state["interval"]
                    save_state(state)
            except (OSError, subprocess.TimeoutExpired):
                # 다른 명령이 상태 파일을 잠근 경우 다음 관측에서 확인한다.
                pass
            time.sleep(5)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("start", "run", "status", "stop", "ack"))
    p.add_argument("request", nargs="?")
    p.add_argument("--thread")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--outcome", choices=("continue", "wait", "stop"), default="wait")
    args = p.parse_args()
    if args.action == "run":
        run()
        return
    with locked(FOLDER / "state.lock"):
        state = read_state()
        if args.action == "start":
            if not args.thread or args.interval < 300:
                p.error("--thread UUID와 300초 이상의 --interval이 필요합니다.")
            uuid.UUID(args.thread)
            if state.get("pending"):
                p.error("이전 요청의 완료 또는 전달 여부를 먼저 확인하세요.")
            codex = shutil.which("codex")
            if not codex:
                p.error("Codex 명령을 찾지 못했습니다.")
            # 실행 중인 감시자가 있으면 중복 실행하지 않는다.
            with locked(FOLDER / "runner.lock"):
                state = dict(enabled=True, thread=args.thread, codex=codex, interval=args.interval,
                             mode="continue", next_at=time.time()+args.interval, pending=None)
                save_state(state)
            executable = Path(sys.executable).with_name("pythonw.exe")
            with (FOLDER / "runner.log").open("ab") as log:
                child = subprocess.Popen([str(executable), str(Path(__file__).resolve()), "run"], cwd=ROOT,
                                         creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=log)
            state["pid"] = child.pid
        elif args.action == "stop":
            state["enabled"] = False
        elif args.action == "ack":
            acknowledge(state, args.request, args.outcome, time.time())
        if args.action != "status":
            save_state(state)
        print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
