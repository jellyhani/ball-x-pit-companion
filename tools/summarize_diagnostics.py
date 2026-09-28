"""상세 로그를 로컬에서 요약한다. 게임·네트워크에 접근하거나 원본 로그를 변경하지 않는다."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def summarize(folder: Path) -> dict:
    events, reasons, channels = Counter(), Counter(), {}
    slowest, latest_states = [], {}
    malformed = 0
    first_time = last_time = None
    paths = [folder / f"diagnostics.jsonl.{index}" for index in range(4, 0, -1)]
    paths.append(folder / "diagnostics.jsonl")
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get("event"), str):
                        raise ValueError("진단 사건 형식 아님")
                except ValueError:
                    malformed += 1
                    continue
                event = row["event"]
                events[event] += 1
                first_time = first_time or row.get("time")
                last_time = row.get("time")
                if row.get("reason"):
                    reasons[f"{event}:{row['reason']}"] += 1
                if event in ("tracking.source", "state.interpreted", "engine.backend", "bridge.installation",
                             "display.hud", "display.base_gate", "harvest.gate"):
                    latest_states[event] = row
                if event == "compute.completed":
                    channel = row.get("channel", "unknown")
                    summary = channels.setdefault(channel, {"count": 0, "total_ms": 0., "max_ms": 0.,
                                                           "compute_total_ms": 0., "measured_count": 0})
                    elapsed = row.get("elapsed_ms")
                    if isinstance(elapsed, (int, float)):
                        summary["count"] += 1
                        summary["total_ms"] += elapsed
                        summary["max_ms"] = max(summary["max_ms"], elapsed)
                        if isinstance(row.get("compute_ms"), (int, float)):
                            summary["compute_total_ms"] += row["compute_ms"]
                            summary["measured_count"] += 1
                        slowest.append(row)
                        slowest.sort(key=lambda entry: -entry["elapsed_ms"])
                        del slowest[10:]
    for summary in channels.values():
        summary["average_ms"] = round(summary["total_ms"] / max(1, summary["count"]), 2)
        summary["compute_average_ms"] = (
            round(summary["compute_total_ms"] / summary["measured_count"], 2)
            if summary["measured_count"] else None
        )
    return dict(first=first_time, last=last_time, events=dict(events), reasons=dict(reasons),
                channels=channels, slowest=slowest, latest_states=latest_states, malformed_lines=malformed)


if __name__ == "__main__":
    from src.services.settings import APP_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=Path(APP_DIR) / "logs")
    args = parser.parse_args()
    print(json.dumps(summarize(args.log_dir), ensure_ascii=False, indent=2))
