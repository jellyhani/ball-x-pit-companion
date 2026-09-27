r"""개선 루프의 읽기 전용 관측. 게임 기록을 수정하지 않고 요약만 build/에 저장한다.

    .venv\Scripts\python.exe tools\improvement_audit.py --record

새 증거는 수정할 이유를 뜻할 뿐, 결함이나 해결을 자동 판정하지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ("harvest.jsonl", "harvest_traces.jsonl", "draws.jsonl", "runs.jsonl")


def tail(path, limit=4_000_000):
    """큰 기록의 끝부분만 읽는다. 첫 행이 잘렸을 때만 버린다."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            start = max(0, size - limit)
            f.seek(start - 1 if start else 0)
            boundary = not start or f.read(1) == b"\n"
            blob = f.read()
        lines = blob.splitlines()
        return lines if boundary else lines[1:]
    except FileNotFoundError:
        return []


def read_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def inspect(profile, previous=None, now=None):
    now = time.time() if now is None else now
    report = {"observed_at": now, "events": {}, "evidence": {}, "warnings": []}
    for name in EVENTS:
        lines = tail(profile / name)
        rows, invalid = [], 0
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("객체가 아닌 기록")
                rows.append(row)
            except ValueError:
                invalid += 1
        latest = rows[-1] if rows else {}
        report["events"][name] = {"rows_in_tail": len(rows), "invalid_rows": invalid,
                                  "last_at": latest.get("at", latest.get("t", latest.get("ended_at")))}
        if name == "harvest.jsonl":
            report["events"][name]["last_gain"] = latest.get("gain")
            report["events"][name]["last_layout"] = latest.get("layout")
        report["evidence"][name] = digest([line.decode("utf-8", errors="replace") for line in lines])
        if invalid:
            report["warnings"].append(f"{name}: 끝부분에 해석하지 못한 행 {invalid}개 (쓰기 중인지 재확인)")
    live_path = profile / "live_state.json"
    live = read_object(live_path)
    base = live.get("base") or {}
    report["live"] = {"readable": bool(live), "plugin": live.get("plugin"), "base_state": base.get("state"),
                      "age_seconds": round(max(0, now - live_path.stat().st_mtime), 1) if live_path.exists() else None}
    # 매 프레임 seq/t, 자동 생산 저장량, 전투 위치만 달라져도 새 조사로 세지 않는다.
    buildings = [{k: b.get(k) for k in ("id", "type", "x", "y", "rot", "worker", "lvl")}
                 for b in base.get("buildings") or []]
    buildings.sort(key=lambda b: str(b.get("id")))
    old = (previous or {}).get("evidence", {})
    report["evidence"]["base_layout"] = digest(buildings) if buildings else old.get("base_layout")
    report["evidence"]["plugin"] = live.get("plugin") or old.get("plugin")
    log_lines = [line.decode("utf-8", errors="replace") for line in tail(profile / "logs/companion.log", 200_000)]
    errors = [line for line in log_lines if " ERROR " in line or "Traceback (most recent call last)" in line]
    layouts = [line for line in log_lines if "배치 추천:" in line]
    report["recent_errors"] = errors[-10:]
    report["last_layout_result"] = layouts[-1] if layouts else None
    report["evidence"]["errors"] = digest(errors[-10:])
    report["changed"] = [key for key, value in report["evidence"].items() if old.get(key) != value]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "")) / "BallxPitCompanion")
    parser.add_argument("--record", action="store_true", help="관측 기준과 요약을 build/improvement 에 저장")
    args = parser.parse_args()
    output = ROOT / "build/improvement"
    previous = read_object(output / "latest.json")
    report = inspect(args.profile, previous)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    report["revision"] = revision
    if previous.get("revision") != revision:
        report["changed"].append("source_revision")
    if args.record:
        output.mkdir(parents=True, exist_ok=True)
        temp = output / "latest.tmp"
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(output / "latest.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
