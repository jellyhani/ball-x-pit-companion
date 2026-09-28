"""이동 완료를 공략 완료로 오인하지 않고 실제 범위·자원 효과를 보존한다."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.engine import layout_opt as lo, layout_guide as guide
from src.engine.layout import grid_from_geo, LayoutPlan
from src.engine.layout_city import guide_report
from tests.test_guide_contract import base_of, bld


class GuideConformanceTest(unittest.TestCase):
    def test_report_uses_game_target_bounds_in_world_units(self):
        base = base_of(bld(1, "kVeteranHut", 2.5, 2.5, range=1),
                       bld(2, "kCozyHome", 4.5, 2.5, range_boxes=[[-1., -.5, 1., .5]]))
        base["geo"]["space_w"] = .5
        grid = grid_from_geo(base["geo"])
        pieces, origins = lo.pieces_from_base(base, grid, lo.housing_types())
        scorer = lo.Scorer(pieces, set(), lo.housing_types())
        self.assertEqual(guide.coverage(grid, pieces, origins, guide.hub_groups(pieces, scorer), 0), 1)
        self.assertIn("1/1", guide_report(grid, pieces, origins, scorer)[0])

    def test_hub_gain_cannot_remove_a_houses_existing_resource_effect(self):
        base = base_of(bld(1, "kVeteranHut", 9.5, 3.5, range=2),
                       bld(2, "kSingleFamilyHome", 2.5, 3.5, range=1.1),
                       bld(3, "kDenseWheat", 1.5, 3.5, cap=14))
        candidate = copy.deepcopy(base)
        candidate["buildings"][1]["x"] = 8.5
        self.assertFalse(guide.preserves_guide(base, candidate))
        candidate["buildings"][2]["x"] = 7.5
        self.assertTrue(guide.preserves_guide(base, candidate))
        candidate["buildings"][2]["cap"] = 1
        self.assertFalse(guide.preserves_guide(base, candidate))

    def test_largest_missing_hub_is_searched_before_combined_repacking(self):
        base = base_of(bld(1, "kVeteranHut", 1.5, 1.5, range=1),
                       bld(2, "kCozyHome", 12.5, 9.5),
                       bld(3, "kCaptainQuarters", 3.5, 1.5, range=1),
                       bld(4, "kBarracks", 12.5, 5.5, stat="kStrength"),
                       bld(5, "kSchoolhouse", 10.5, 5.5, stat="kIntelligence"),
                       bld(6, "kShoemaker", 8.5, 5.5, stat="kSpeed"))
        with patch.object(guide, "repair", return_value=None) as repair, \
                patch("src.engine.native_layout._lib", return_value=None):
            lo.optimize(base, preset="guide", seconds=.5, restarts=0)
        first_groups = repair.call_args_list[0].args[3]
        self.assertEqual([hub.type for hub, members in first_groups], ["kCaptainQuarters"])

    def test_finished_moves_show_remaining_guide_coverage_on_hud(self):
        from src.app_controller import AppController
        base = base_of(bld(1, "kVeteranHut", 1.5, 1.5, range=1),
                       bld(2, "kCozyHome", 10.5, 5.5))
        plan = LayoutPlan(0, 0, final={1: (1.5, 1.5), 2: (10.5, 5.5)})
        context = SimpleNamespace(window=SimpleNamespace(rect=(0, 0, 1280, 720), size=(1280, 720)),
                                  settings=SimpleNamespace(hud_auto_show=True), user_hidden=False,
                                  _game_active=lambda: True, _homography=lambda _: None,
                                  layout_plan=plan, _remaining_moves=lambda _: [], base_overlay=Mock())
        AppController._render_base(context, base, "kRearrangeBuildings")
        lines = context.base_overlay.show_advice.call_args.args[4]
        self.assertIn("이번 이동 완료", lines[0][0])
        self.assertTrue(any("0/1" in text for text, _ in lines))
        self.assertFalse(any("가이드 배치 완료" in text for text, _ in lines))


if __name__ == "__main__":
    unittest.main()
