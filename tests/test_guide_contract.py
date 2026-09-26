"""공략의 중첩 효과·입구·기존 생산을 점수 합으로 희생시키지 않는다."""
import copy
import unittest
from unittest.mock import patch

from src.engine import layout_guide as lg, layout_opt as lo
from src.engine.layout import grid_from_geo


def bld(i, kind, x, y, **kwargs):
    return dict(id=i, type=kind, x=x, y=y, tw=1, th=1, rot=0,
                **dict(dict(range=2, lvl=1, state="kNormal", stat="kNum"), **kwargs))


def base_of(*buildings):
    return {"geo": {"space_w": 1.0, "chunk_w": 16, "chunk_h": 12, "chunks": [[0, 0]],
                    "left": 0.0, "bottom": 0.0, "colliders": []}, "buildings": list(buildings)}


class GuideContractTest(unittest.TestCase):
    def tearDown(self):
        lo.set_char_levels({})

    def test_statue_and_stat_house_belong_to_both_effects(self):
        base = base_of(bld(1, "kBrickHouse", 3.5, 3.5), bld(2, "kCaptainQuarters", 4.5, 3.5),
                       bld(3, "kVeteranHut", 5.5, 3.5),
                       bld(4, "kStrengthStatue", 3.5, 4.5, stat="kStrength"),
                       bld(5, "kSingleFamilyHome", 4.5, 4.5, stat="kStrength"),
                       bld(6, "kIdleLumberyard", 2.5, 3.5, state="kScaffold"))
        grid = grid_from_geo(base["geo"])
        pieces, origin = lo.pieces_from_base(base, grid, lo.housing_types())
        scorer = lo.Scorer(pieces, lo._stat_types(base), lo.housing_types())
        groups = lg.hub_groups(pieces, scorer)
        by_type = {hub.type: set(members) for hub, members in groups}
        self.assertTrue({4, 5} <= by_type["kCaptainQuarters"])
        self.assertTrue({4, 6} <= by_type["kBrickHouse"])
        self.assertIn(5, by_type["kVeteranHut"])
        from src.engine.layout_city import guide_report
        report = guide_report(grid, pieces, origin, scorer)
        self.assertTrue(any("능력치 건물 2/2" in line for line in report), report)

    def test_same_total_does_not_justify_losing_an_existing_resident(self):
        base = base_of(bld(1, "kVeteranHut", 3.5, 3.5), bld(2, "kCozyHome", 4.5, 3.5),
                       bld(3, "kSingleFamilyHome", 10.5, 3.5))
        candidate = copy.deepcopy(base)
        candidate["buildings"][1]["x"], candidate["buildings"][2]["x"] = 10.5, 4.5
        self.assertFalse(lg.preserves_guide(base, candidate))

    def test_empty_entrance_cannot_be_exchanged_for_effect_gain(self):
        base = base_of(bld(1, "kClinic", .5, 5.5))
        base["geo"]["entrance_chunk"] = [0, 0]
        grid = grid_from_geo(base["geo"])
        entrance = lo.entrance_cells(base["geo"], grid)
        self.assertTrue(entrance)
        at = next(iter(entrance))
        candidate = copy.deepcopy(base)
        candidate["buildings"][0].update(zip(("x", "y"), grid.center(*at, 1, 1)))
        self.assertFalse(lg.preserves_guide(base, candidate))

    def test_game_range_is_not_expanded_again_from_guide_claim(self):
        base = base_of(bld(1, "kVilla", 3.5, 3.5, lvl=2, range=2.25))
        pieces, _ = lo.pieces_from_base(base, grid_from_geo(base["geo"]), lo.housing_types())
        self.assertEqual(pieces[1].range, 2.25)

    def test_explicit_no_stat_is_not_replaced_by_historical_defaults(self):
        base = base_of(bld(1, "kClinic", 3.5, 3.5, stat="kNum"))
        self.assertEqual(lo._stat_types(base), set())
        base["buildings"][0].pop("stat")
        self.assertEqual(lo._stat_types(base), {"kClinic"})

    def test_active_empty_resource_house_gets_single_free_move(self):
        farm = bld(1, "kIdleFarm", 3.5, 3.5, worker=0, range=2.1,
                   in_range={"kDenseWheat": 2})
        house = bld(2, "kSingleFamilyHome", 12.5, 9.5, range=1.1,
                    in_range={"kDenseWheat": 0})
        base = base_of(farm, house, bld(3, "kDenseWheat", 2.5, 3.5, cap=4),
                       bld(4, "kDenseWheat", 4.5, 3.5, cap=4))
        original = copy.deepcopy(base)
        # 임의 담금질 성공 여부가 아닌, 현재 게임 계수로 만든 단독 이동 경로를 확인한다.
        with patch.object(lo, "anneal", side_effect=lambda lay, *a, **k: (dict(lay.origin), 0)), \
                patch.object(lo, "polish", return_value=0):
            # 후보 준비도 시간 예산에 포함되므로 양수 예산을 준다. 담금질은 계속 무효화해 단독 이동만 검증한다.
            plan = lo.optimize(base, preset="guide", seconds=.25, restarts=1)
        after = lo.final_base(base, plan)
        self.assertEqual(plan.moved, 1)
        self.assertNotEqual(plan.origin_after[2], plan.origin_before[2])
        self.assertTrue(lo.preserves_production(base, after))
        self.assertTrue(lg.preserves_guide(base, after))
        self.assertEqual(base, original)

    def test_neutral_cost_does_not_become_a_committed_resource_goal(self):
        from types import SimpleNamespace
        from src.app_controller import AppController
        from src.engine.base_advisor import BaseSuggestion
        from src.tracking.meta_state import MetaState
        from tests.helpers import game_data
        state = SimpleNamespace(meta=MetaState(resources=(0, 0, 0, 0)), data=game_data(), _base_snap=None)
        neutral = BaseSuggestion("upgrade", "kClinic", "", "", "", False, "", False, "", cost=(0, 0, 0, 99))
        core = BaseSuggestion("build", "kVeteranHut", "", "", "", False, "", False, "", cost=(0, 2, 0, 0),
                              priority_group="guide_core")
        with patch("src.app_controller.suggest_base", return_value=[neutral, core]):
            self.assertEqual(AppController._shortfalls(state), {"밀": 2})

    def test_layout_uses_current_game_duration_before_any_aim(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from src.app_controller import AppController
        from src.tracking.meta_state import MetaState
        submit = Mock()
        state = SimpleNamespace(_base_snap={"buildings": [], "geo": {"colliders": [{"id": 1}], "harvest_len": 9}},
                                meta=MetaState(resources=(100, 20, 10, 5)), _layout_busy=False,
                                _harvest_dur=16, layout_plan=None, sim=SimpleNamespace(submit=submit),
                                _shortfalls=lambda: {}, aim_range=SimpleNamespace(limits=(25, 155)),
                                _layout_fingerprint=lambda _b: "test-key", _sim_req={})
        AppController.compute_layout(state)
        self.assertEqual(submit.call_args.args[5], 9)
