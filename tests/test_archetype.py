"""덱 계열 감지 (게임 데이터 볼 태그)."""
import unittest

from src.engine.archetype import axes_of, detect
from src.gamedata import load_game_data
from src.tracking.run_state import Owned, RunState


class ArchetypeTest(unittest.TestCase):
    def setUp(self):
        self.d = load_game_data()

    def test_axes_from_game_tags(self):
        self.assertEqual(axes_of(self.d, "ball:burn"), ["burn"])
        self.assertIn("aoe", axes_of(self.d, "ball:earthquake"))     # 위키 태그·게임 설명 어느 쪽이든 범위
        self.assertIn("burn", axes_of(self.d, "ball:magma"))
        self.assertEqual(axes_of(self.d, "passive:armor"), [])

    def test_detects_burn_deck(self):
        run = RunState()
        run.start_run()
        for iid in ("ball:burn", "ball:magma"):
            run.owned[iid] = Owned(iid, "ball", 2, "game")
        run.owned["ball:freeze"] = Owned("ball:freeze", "ball", 1, "game")
        arch = detect(run, self.d)
        self.assertEqual(arch.top[0], "burn")
        self.assertNotIn("freeze", arch.top)

    def test_no_archetype_with_one_ball(self):
        run = RunState()
        run.start_run()
        run.owned["ball:bleed"] = Owned("ball:bleed", "ball", 1, "game")
        self.assertEqual(detect(run, self.d).top, [])

    def test_combined_ball_axes_are_counted(self):
        # 산사태(범위)에 빙하(빙결)를 합쳐 넣으면 산사태는 없어진 빙하의 계열도 갖는다
        self.assertIn("aoe", axes_of(self.d, "ball:landslide"))
        self.assertEqual(axes_of(self.d, "ball:glacier"), ["freeze"])
        run = RunState()
        run.start_run()
        run.owned["ball:landslide"] = Owned("ball:landslide", "ball", 1, "game", combined=("ball:glacier",))
        arch = detect(run, self.d)
        self.assertIn("freeze", arch.scores)      # 합쳐 넣어 사라진 빙하의 계열도 점수에 들어간다
        self.assertIn("aoe", arch.scores)         # 산사태 자신의 계열은 그대로 유지


class CharacterSeedTest(unittest.TestCase):
    """두 캐릭터 성향을 덱 계열 점수에 합산 (strategy.favor 의 aoe/baby/sustain)."""

    def setUp(self):
        self.d = load_game_data()
        rules = {"char:a": {"strategy": {"favor": {"aoe": 3}}},
                 "char:b": {"strategy": {"favor": {"baby": 4, "crit": 5}}},
                 "char:c": {"strategy": {"favor": {"aoe": 3}}},
                 "char:weak": {"strategy": {"favor": {"aoe": 2}}}}
        self._orig = self.d.character_rule
        self.d.character_rule = lambda cid: rules.get(cid, {})

    def tearDown(self):
        self.d.character_rule = self._orig

    def _run(self, *ids):
        run = RunState()
        run.start_run()
        run.characters = [(c, "manual") for c in ids]
        return run

    def test_both_characters_count(self):
        arch = detect(self._run("char:a", "char:b"), self.d)
        self.assertEqual(arch.scores, {"aoe": 2.0, "baby": 2.0})
        self.assertEqual(arch.top, ["aoe", "baby"])          # 동점은 이름순 — 결정적

    def test_order_does_not_matter(self):
        ab = detect(self._run("char:a", "char:b"), self.d)
        ba = detect(self._run("char:b", "char:a"), self.d)
        self.assertEqual((ab.scores, ab.top), (ba.scores, ba.top))

    def test_same_axis_adds_and_duplicate_id_counts_once(self):
        self.assertEqual(detect(self._run("char:a", "char:c"), self.d).scores["aoe"], 4.0)
        self.assertEqual(detect(self._run("char:a", "char:a"), self.d).scores["aoe"], 2.0)

    def test_weak_unknown_and_empty_do_nothing(self):
        for ids in (("char:weak",), ("char:none",), ()):
            self.assertEqual(detect(self._run(*ids), self.d).scores, {})

    def test_stays_after_balls_but_does_not_override(self):
        run = self._run("char:a")                            # 범위 성향
        run.owned["ball:burn"] = Owned("ball:burn", "ball", 10, "game")    # 화상 전용 볼 (점수 3.7)
        arch = detect(run, self.d)
        self.assertEqual(arch.top[0], "burn")                # 자란 화상 덱을 캐릭터가 덮어쓰지 않는다
        self.assertEqual(arch.scores["aoe"] - detect(self._run(), self.d).scores.get("aoe", 0), 2.0)


if __name__ == "__main__":
    unittest.main()
