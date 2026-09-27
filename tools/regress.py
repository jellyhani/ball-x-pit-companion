"""회귀 검사: 저장된 게임 연동 스냅샷을 추천 경로에 다시 넣고, 기대 결과와 비교한다.

게임 업데이트나 규칙 수정 뒤 추천이 의도치 않게 바뀌었는지 확인하는 용도다.

    .venv\\Scripts\\python.exe tools\\regress.py              기본 묶음(tests/fixtures) 비교
    .venv\\Scripts\\python.exe tools\\regress.py --live       이 PC에 쌓인 최근 스냅샷도 함께 돌려 오류만 확인
    .venv\\Scripts\\python.exe tools\\regress.py --update     기대 결과를 지금 결과로 바꾼다 (의도한 변경일 때만)

스냅샷마다 런 상태를 새로 만든다(보유 목록·캐릭터는 스냅샷 값). 이전 선택 이력·내 기록은 쓰지 않는다.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BXP_CATALOG_FILE", os.path.join(ROOT, "tests", "fixtures", "game_recipes.json"))  # PC 마다 같게
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "bridge_snapshots.jsonl")
EXPECTED = os.path.join(ROOT, "tests", "fixtures", "bridge_snapshots.expected.json")


def load(path: str) -> List[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def replay(msgs: List[dict]) -> List[dict]:
    """메시지 묶음(catalog 가 앞에 올 수 있음)을 돌려 스냅샷마다 요약을 만든다."""
    from src.engine.recommender import Recommender, card_verdict
    from src.gamedata import load_game_data, apply_catalog
    from src.tracking.bridge_adapter import convert
    from src.tracking.choice_tracker import ChoiceTracker
    from src.tracking.run_state import RunState

    data = load_game_data()
    data.set_observed_max_level("ball", 3)
    data.set_observed_max_level("passive", 3)
    out = []
    for m in msgs:
        if isinstance(m.get("catalog"), dict):
            apply_catalog(data, m["catalog"])
            continue
        if not isinstance(m.get("levelup"), dict):
            continue
        snap = json.loads(json.dumps(m))
        snap["levelup"]["page"] = "kSelect"
        st = convert(snap, data, (0, 0), frame_id=1, at=0)
        obs = st.observation
        run = RunState()
        run.start_run()
        if obs.inventory is not None:
            run.apply_inventory(obs.inventory, data)
        run.apply_character(obs.character_id, obs.extra_characters, "game")
        run.damage = dict(st.damage)
        evs = [e for e in ChoiceTracker().observe(obs, time.monotonic(), immediate=True) if e.kind == "opened"]
        if not evs:
            out.append({"seq": m.get("seq"), "error": "선택창으로 인식 안 됨"})
            continue
        rec = Recommender(data).recommend(evs[0].session, run)
        out.append({
            "seq": m.get("seq"),
            "status": rec.status,
            "best": rec.best.card.item_id if rec.best else None,
            "cards": [[e.card.item_id, card_verdict(rec, e)] for e in rec.evals],
            "banish": rec.banish_card.item_id if rec.banish_card else None,
            "reroll": rec.reroll_status,
        })
    return out


def compare(expected: List[dict], got: List[dict]) -> List[str]:
    diffs = []
    # 결과가 통째로 사라지거나 중복되면 내용 비교만으로는 회귀를 놓칠 수 있다.
    for label, rows in (("기대 결과", expected), ("실제 결과", got)):
        seen = set()
        for row in rows:
            seq = row.get("seq")
            if seq is None:
                diffs.append(f"{label}: seq 누락")
            elif seq in seen:
                diffs.append(f"{label}: seq {seq} 중복")
            seen.add(seq)
    exp = {e.get("seq"): e for e in expected}
    actual = {g.get("seq") for g in got}
    for seq in exp:
        if seq not in actual:
            diffs.append(f"seq {seq}: 실제 결과 누락")
    for g in got:
        e = exp.get(g.get("seq"))
        if e is None:
            diffs.append(f"seq {g.get('seq')}: 기대 결과 없음 (--update 필요)")
        elif e != g:
            keys = [k for k in set(e) | set(g) if e.get(k) != g.get(k)]
            diffs.append(f"seq {g.get('seq')}: " + "; ".join(f"{k} {e.get(k)} → {g.get(k)}" for k in sorted(keys)))
    return diffs


def main(argv: List[str]) -> int:
    got = replay(load(FIXTURE))
    if "--update" in argv:
        with open(EXPECTED, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=1)
        print(f"기대 결과 갱신: {len(got)}개")
        return 0
    with open(EXPECTED, encoding="utf-8") as f:
        expected = json.load(f)
    diffs = compare(expected, got)
    print(f"기본 묶음 {len(got)}개 · 달라진 것 {len(diffs)}개")
    for d in diffs:
        print("  " + d)
    errors = 0
    if "--live" in argv:
        folder = os.path.join(os.environ.get("LOCALAPPDATA", ""), "BallxPitCompanion", "snapshots")
        files = sorted(glob.glob(os.path.join(folder, "*.jsonl")))[-7:]
        for path in files:
            res = replay(load(path))
            bad = [r for r in res if r.get("error")]
            errors += len(bad)
            print(f"{os.path.basename(path)}: {len(res)}개, 오류 {len(bad)}개")
            for r in bad[:5]:
                print(f"  seq {r.get('seq')}: {r['error']}")
    return 1 if diffs or errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
