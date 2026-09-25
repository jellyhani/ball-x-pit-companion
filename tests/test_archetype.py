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


if __name__ == "__main__":
    unittest.main()
