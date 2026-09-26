"""게임 연동(BepInEx 브리지) 수신 쪽 검사.

스냅샷 형식은 실제 플러그인이 보낸 메시지(런 밖)와 게임 interop 의 필드 이름을 따른 합성 입력이다.
파이프 수신 검사는 테스트 안에서 진짜 Windows named pipe 서버를 만들어 한다.
"""
import ctypes
import json
import sys
import threading
import time
import unittest
from ctypes import wintypes

from PySide6.QtWidgets import QApplication

from src.domain import CardLabel, ScreenKind
from src.services.bridge_client import BridgeClient
from src.tracking.bridge_adapter import convert, infer_pick
from src.tracking.choice_tracker import ChoiceTracker
from tests.helpers import game_data

app = QApplication.instance() or QApplication(sys.argv)

# 실제 게임 화면 2(삭제 버튼 있는 선택창)와 같은 상황을 게임 내부 이름으로 적은 스냅샷
LEVELUP = {
    "v": 1, "seq": 7, "t": 100.0, "game_version": "1.301", "screen_w": 1920, "screen_h": 1080,
    "game_state": "kLevelUp",
    "battle": {"char": "kRecaller", "level": "kSnowy", "turn": 12, "gold": 12, "health": 80, "upgrade_lvl": 4,
               "level_ups_avail": 1, "free_rerolls": 0, "banishes": 2, "rerolls": 0,
               "balls": [{"idx": 0, "type": "kFreeze", "lvl": 0}, {"idx": 1, "type": "kHeavy", "lvl": 0},
                         {"idx": 2, "type": "kLaserHorz", "lvl": 0}],   # 게임 lvl 은 0부터 (화면 1)
               "passives": []},
    "levelup": {"type": "kNormal", "page": "kSelection", "reroll_cost": 5, "panel": [0, 0, 935, 1080],
                "choices": [
                    {"idx": 2, "kind": "kHero", "is_new": False, "equip_idx": 0, "type": "kFreeze", "tgt_lvl": 2,
                     "rect": [635, 575, 260, 260]},
                    {"idx": 0, "kind": "kHero", "is_new": True, "equip_idx": -1, "type": "kLaserHorz", "tgt_lvl": 1,
                     "rect": [35, 575, 260, 260]},
                    {"idx": 1, "kind": "kPassive", "is_new": True, "equip_idx": -1, "type": "kEtherealCloak",
                     "tgt_lvl": 1, "rect": [335, 575, 260, 260]}]},
}


class AdapterTest(unittest.TestCase):
    def test_levelup_snapshot(self):
        st = convert(LEVELUP, game_data(), origin=(100, 50))
        obs = st.observation
        self.assertTrue(st.in_run)
        self.assertEqual(obs.kind, ScreenKind.LEVEL_UP)
        # 화면 왼쪽부터 정렬, 위치 이름은 게임 화면 기준
        self.assertEqual([(c.position, c.item_id) for c in obs.cards],
                         [("왼쪽", "ball:laserhorz"), ("가운데", "passive:etherealcloak"), ("오른쪽", "ball:freeze")])
        self.assertEqual([c.label for c in obs.cards], [CardLabel.NEW, CardLabel.NEW, CardLabel.UPGRADE])
        self.assertEqual(obs.cards[2].shown_level, 2)
        self.assertEqual(obs.character_id, "char:recaller")
        self.assertEqual((obs.gold, obs.reroll_cost, obs.banish_left, obs.free_rerolls), (12, 5, 2, None))
        self.assertEqual([(s.item_id, s.level) for s in obs.inventory],
                         [("ball:freeze", 1), ("ball:heavy", 1), ("ball:laserhorz", 1)])
        self.assertEqual(obs.frame.to_screen(obs.cards[0].rect), (135, 625, 260, 260))

    def test_out_of_run_snapshot(self):
        snap = {"v": 1, "seq": 1, "screen_w": 1920, "screen_h": 1080, "game_state": None,
                "battle": None, "levelup": None}
        st = convert(snap, game_data())
        self.assertFalse(st.in_run)
        self.assertEqual(st.observation.kind, ScreenKind.OTHER)

    def test_unknown_game_type_is_reported_not_guessed(self):
        snap = json.loads(json.dumps(LEVELUP))
        snap["levelup"]["choices"][0]["type"] = "kAddHeroDamage"
        st = convert(snap, game_data())
        self.assertIn("kAddHeroDamage", st.unknown_types)
        self.assertEqual(sum(1 for c in st.observation.cards if c.item_id is None), 1)

    def test_pick_inferred_from_inventory_change(self):
        d = game_data()
        before = convert(LEVELUP, d).observation
        after_snap = json.loads(json.dumps(LEVELUP))
        after_snap["battle"]["balls"][0]["lvl"] = 1          # 냉동 강화를 골랐다 (화면 레벨 1 → 2)
        after_snap["levelup"] = None
        after = convert(after_snap, d).observation
        self.assertEqual(infer_pick(before.inventory, after.inventory, before.cards).item_id, "ball:freeze")
        # 레이저(수평) 복사본을 고른 경우: 같은 볼이 하나 더 생긴다
        dup = json.loads(json.dumps(LEVELUP))
        dup["battle"]["balls"].append({"idx": 3, "type": "kLaserHorz", "lvl": 0})
        dup["levelup"] = None
        self.assertEqual(infer_pick(before.inventory, convert(dup, d).observation.inventory, before.cards).item_id,
                         "ball:laserhorz")

    def test_combined_balls_are_parsed(self):
        # 실제 기록(companion.log, 2026-09-25 19:xx): 산사태(kLandslide)에 피뢰침(kLightningRod)을 합쳐 넣음
        snap = json.loads(json.dumps(LEVELUP))
        snap["battle"]["balls"] = [{"idx": 0, "type": "kLandslide", "lvl": 1, "combined": ["kLightningRod"]},
                                   {"idx": 1, "type": "kHeavy", "lvl": 0}]
        snap["levelup"] = None
        obs = convert(snap, game_data()).observation
        self.assertEqual(obs.inventory[0].item_id, "ball:landslide")
        self.assertEqual(obs.inventory[0].combined, ("ball:lightningrod",))
        self.assertEqual(obs.inventory[1].combined, ())   # 합친 게 없으면 빈 튜플

    def test_combined_field_missing_is_backward_compatible(self):
        # 옛 플러그인(1.11 미만)은 볼 항목에 combined 자체가 없다
        snap = json.loads(json.dumps(LEVELUP))
        snap["levelup"] = None
        obs = convert(snap, game_data()).observation
        self.assertTrue(all(s.combined == () for s in obs.inventory))

    def test_tracker_opens_and_closes_immediately(self):
        d = game_data()
        tr = ChoiceTracker()
        ev = tr.observe(convert(LEVELUP, d).observation, 0.0, immediate=True)
        self.assertEqual(ev[0].kind, "opened")
        closed = json.loads(json.dumps(LEVELUP))
        closed["levelup"] = None
        ev = tr.observe(convert(closed, d).observation, 0.2, immediate=True)
        self.assertEqual(ev[0].kind, "closed")

    def test_reroll_detected_by_gold_spent(self):
        d = game_data()
        tr = ChoiceTracker()
        tr.observe(convert(LEVELUP, d).observation, 0.0, immediate=True)
        rer = json.loads(json.dumps(LEVELUP))
        rer["battle"]["gold"] = 7
        rer["levelup"]["choices"][0]["type"] = "kBurn"
        rer["levelup"]["choices"][0]["is_new"] = True
        ev = tr.observe(convert(rer, d).observation, 0.4, immediate=True)
        self.assertEqual([e.kind for e in ev], ["closed", "opened"])
        self.assertEqual(ev[0].outcome.kind, "rerolled")


FUSER = {
    "v": 1, "seq": 9, "screen_w": 1920, "screen_h": 1080, "game_state": "kLevelUp",
    "battle": {"char": "kItchyFinger", "chars_combined": [], "gold": 30, "banishes": 2, "free_rerolls": 0,
               "balls": [{"idx": 0, "type": "kBleed", "lvl": 2, "max": True},
                         {"idx": 1, "type": "kHeavy", "lvl": 2, "max": True},
                         {"idx": 2, "type": "kBurn", "lvl": 2, "max": True}],
               "passives": []},
    "levelup": {"type": "kFuser", "page": "kFuser", "reroll_cost": 0, "choices": [],
                "fuser": {"free_upgrades": True, "options": ["kFreeUpgrades", "kCombo", "kEvo"],
                          "evos": [{"idx": 0, "equip_idx": 0, "evo_idx": 0, "type": "kHemorrhage"}],
                          "combos": [{"idx": 0, "h1": "kBleed", "h2": "kBurn", "idx1": 0, "idx2": 2,
                                      "ai_score": 42.0, "bad": False},
                                     {"idx": 1, "h1": "kHeavy", "h2": "kBurn", "idx1": 1, "idx2": 2,
                                      "ai_score": 10.0, "bad": True}]}},
}


class FusionTest(unittest.TestCase):
    def test_fuser_snapshot_and_advice(self):
        from src.engine.fusion import FusionAdvisor
        from src.tracking.run_state import RunState
        d = game_data()
        st = convert(FUSER, d)
        obs = st.observation
        self.assertEqual(obs.kind, ScreenKind.FUSION)
        self.assertEqual(st.max_ball_level, 3)          # 게임이 max=True 라고 한 볼의 화면 레벨
        self.assertEqual(obs.fuser.evos[0].item_id, "ball:hemorrhage")
        self.assertEqual([(c.item1, c.item2, c.bad) for c in obs.fuser.combos],
                         [("ball:bleed", "ball:burn", False), ("ball:heavy", "ball:burn", True)])
        run = RunState()
        run.start_run()
        run.apply_inventory(obs.inventory, d)
        rec = FusionAdvisor(d).recommend(obs.fuser, obs.inventory, run)
        self.assertEqual(rec.best.kind, "evo")
        self.assertEqual(rec.best.result_id, "ball:hemorrhage")
        self.assertIn("출혈 + 무쇠", rec.best.detail)
        # 게임이 나쁜 조합으로 판정한 융합은 경고가 붙고 아래로 간다
        self.assertEqual(rec.combos[-1].warnings[0].rule_id, "combo_bad")
        self.assertEqual(rec.combos[0].title, "출혈 + 화상")

    def test_real_fuser_screen_is_pick_treasure(self):
        # 실제 융합 화면: game_state 는 kPickTreasure, ui.screen 은 fuser
        snap = dict(FUSER, game_state="kPickTreasure", ui={"screen": "fuser", "avoid": [[375, 120, 600, 840]]})
        self.assertEqual(convert(snap, game_data()).observation.kind, ScreenKind.FUSION)

    def test_fuser_dropping_on_field_is_ignored(self):
        # 융합기가 필드에 떨어질 때: kPlaying + kFuser 인데 융합 UI 는 활성 아님
        for extra in ({}, {"ui": {"screen": None}}):
            snap = dict(FUSER, game_state="kPlaying", **extra)
            self.assertNotEqual(convert(snap, game_data()).observation.kind, ScreenKind.FUSION)

    def test_game_recipes_replace_wiki(self):
        from src.gamedata import load_game_data
        from src.tracking.bridge_adapter import catalog_recipes
        d = load_game_data()      # 공유 데이터를 바꾸지 않도록 새로 읽는다
        cat = {"balls": [{"type": "kHemorrhage", "in_game": True, "recipes": [["kBleed", "kHeavy"]]},
                         {"type": "kBleed", "in_game": True, "recipes": []}],
               "passives": [{"type": "kArgentStopwatch", "in_game": True,
                             "recipes": [["kHourglass", "kSilverBullet"]]}]}
        n = d.apply_game_recipes(catalog_recipes(cat, d))
        self.assertEqual(n, 2)
        self.assertEqual(d.recipe_source, "game")
        self.assertEqual([r.result for r in d.recipes_using("ball:bleed")], ["ball:hemorrhage"])


# ---- 진짜 named pipe 로 수신 검사 ----
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateNamedPipeW.restype = wintypes.HANDLE
k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                          ctypes.c_void_p]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def serve(name: str, payloads, ready: threading.Event):
    h = k32.CreateNamedPipeW(name, 0x00000002, 0, 1, 65536, 65536, 0, None)   # PIPE_ACCESS_OUTBOUND
    ready.set()
    k32.ConnectNamedPipe(h, None)
    for p in payloads:
        data = p.encode("utf-8")
        n = wintypes.DWORD()
        k32.WriteFile(h, data, len(data), ctypes.byref(n), None)
        time.sleep(0.05)
    time.sleep(0.3)
    k32.CloseHandle(h)


class PipeTest(unittest.TestCase):
    def test_receives_split_and_bad_lines(self):
        name = r"\\.\pipe\ballxpit-bridge-test-" + str(int(time.time() * 1000))
        good = json.dumps(LEVELUP)
        ready = threading.Event()
        # 한 메시지가 두 번에 나뉘어 오고, 중간에 깨진 줄과 다른 버전이 섞여 있다
        payloads = [good[:40], good[40:] + "\n", "{깨진 줄\n", json.dumps({"v": 99}) + "\n", good + "\n"]
        t = threading.Thread(target=serve, args=(name, payloads, ready), daemon=True)
        t.start()
        ready.wait(2)
        got = []
        client = BridgeClient(name)
        client.snapshot.connect(lambda m, at: got.append(m))
        client.start()
        end = time.time() + 4
        while time.time() < end and len(got) < 2:
            app.processEvents()
            time.sleep(0.02)
        client.stop()
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["battle"]["char"], "kRecaller")


if __name__ == "__main__":
    unittest.main()
