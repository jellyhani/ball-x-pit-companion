"""합성 입력: 게임 가능 후보와 Steam 완성형 공략의 선택을 혼동하지 않는다."""
import copy
import unittest

from src.engine.base_advisor import suggest
from src.engine.construction_policy import EXCLUDED_BUILDINGS
from src.tracking.meta_state import parse_meta
from tests.helpers import game_data


def blueprint(kind, cost=(10, 0, 0, 0), category="kHousing"):
    return {"type": kind, "cost": list(cost), "cat": category, "tw": 2, "th": 2}


class ConstructionGuidePolicyTest(unittest.TestCase):
    def recommendations(self, raw, base=None):
        data = game_data()
        return suggest(parse_meta(raw, data), data, limit=100, base=base)

    def test_user_and_guide_exclusions_cover_build_upgrade_and_finish(self):
        raw = {"resources": [1000, 100, 100, 100], "blueprints": [], "buildings": []}
        for kind in ("kGoldMine", "kIdleLauncher", "kWarRoom"):
            raw["blueprints"].append(blueprint(kind))
            raw["buildings"] += [{"type": kind, "state": "kScaffold"},
                                  {"type": kind, "state": "kUpgrading"},
                                  {"type": kind, "state": "kNormal", "can_upgrade": True,
                                   "upgrade_cost": [10, 0, 0, 0]}]
        before = copy.deepcopy(raw)
        self.assertEqual(self.recommendations(raw), [])
        self.assertEqual(EXCLUDED_BUILDINGS, {"kGoldMine", "kIdleLauncher", "kWarRoom"})
        self.assertEqual(raw, before)

    def test_core_hubs_are_one_conditional_group_not_a_fixed_build_order(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kCozyHome", "state": "kNormal"},
                             {"type": "kEnduranceStatue", "state": "kNormal"}],
               "blueprints": [blueprint(k) for k in ("kCaptainQuarters", "kBrickHouse", "kVeteranHut", "kBank")]}
        rows = self.recommendations(raw)
        core = {s.type: s for s in rows if s.priority_group == "guide_core"}
        self.assertEqual(set(core), {"kCaptainQuarters", "kBrickHouse", "kVeteranHut"})
        self.assertTrue(all(s.source.startswith("Steam Zarcos") and s.reason for s in core.values()))
        self.assertEqual({s.type for s in rows[:3]}, set(core))
        self.assertEqual(rows[-1].priority_group, "neutral")
        self.assertEqual(rows[-1].source, "")

    def test_core_hubs_without_confirmed_targets_remain_neutral(self):
        raw = {"resources": [1000, 100, 100, 100], "buildings": [],
               "blueprints": [blueprint(k) for k in ("kCaptainQuarters", "kBrickHouse", "kVeteranHut")]}
        rows = self.recommendations(raw)
        self.assertTrue(all(s.priority_group == "neutral" and not s.source for s in rows))

    def test_runtime_stat_house_prioritizes_captain_but_not_fortress(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kNewStatHouse", "state": "kNormal"}],
               "blueprints": [blueprint("kCaptainQuarters"), blueprint("kBrickHouse")]}
        base = {"buildings": [{"type": "kNewStatHouse", "stat": "kStrength"}]}
        before = copy.deepcopy(base)
        rows = {s.type: s for s in self.recommendations(raw, base)}
        self.assertEqual(rows["kCaptainQuarters"].priority_group, "guide_core")
        self.assertNotIn("무한 강화", rows["kCaptainQuarters"].reason)
        self.assertEqual(rows["kBrickHouse"].priority_group, "neutral")
        self.assertEqual(base, before)

    def test_missing_live_stat_uses_only_owned_known_stat_buildings(self):
        for owned, expected in (("kBarracks", "guide_core"), ("kBank", "neutral")):
            with self.subTest(owned=owned):
                raw = {"resources": [1000, 100, 100, 100],
                       "buildings": [{"type": owned, "state": "kNormal"}],
                       "blueprints": [blueprint("kCaptainQuarters"), blueprint("kBrickHouse")]}
                rows = {s.type: s for s in self.recommendations(raw)}
                self.assertEqual(rows["kCaptainQuarters"].priority_group, expected)
                self.assertEqual(rows["kBrickHouse"].priority_group, "neutral")

    def test_game_none_stat_is_authoritative_and_unowned_runtime_type_is_ignored(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kBarracks", "state": "kNormal"}],
               "blueprints": [blueprint("kCaptainQuarters")]}
        for marker in ("kNum", "kNone", "kInvalid", "kCount", "kMax"):
            with self.subTest(marker=marker):
                base = {"buildings": [{"type": "kBarracks", "stat": marker},
                                      {"type": "kNotOwned", "stat": "kStrength"}]}
                self.assertEqual(self.recommendations(raw, base)[0].priority_group, "neutral")

    def test_active_construction_makes_fortress_relevant_but_retired_gold_does_not(self):
        for target, expected in (("kIdleFarm", "guide_core"), ("kGoldMine", "neutral")):
            with self.subTest(target=target):
                raw = {"resources": [1000, 100, 100, 100],
                       "buildings": [{"type": target, "state": "kScaffold"}],
                       "blueprints": [blueprint("kBrickHouse")]}
                row = next(s for s in self.recommendations(raw) if s.type == "kBrickHouse")
                self.assertEqual(row.priority_group, expected)

    def test_core_buildings_do_not_imply_their_upgrades_increase_guide_effect(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kCozyHome", "state": "kNormal"},
                             {"type": "kVeteranHut", "state": "kNormal", "can_upgrade": True,
                              "upgrade_cost": [10, 0, 0, 0]}]}
        rows = self.recommendations(raw)
        self.assertEqual([(s.kind, s.priority_group) for s in rows], [("upgrade", "neutral")])

    def test_mansion_is_deferred_without_deferring_unfinished_completion(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kMansion", "state": "kScaffold"}],
               "blueprints": [blueprint("kMansion"), blueprint("kBank")]}
        rows = self.recommendations(raw)
        self.assertEqual(rows[0].priority_group, "finish")
        self.assertEqual(rows[-1].type, "kMansion")
        self.assertEqual(rows[-1].priority_group, "guide_later")
        self.assertEqual(rows[-1].source, "Steam Zarcos")

    def test_game_cost_and_description_are_separate_from_guide_reason(self):
        data = game_data()
        raw = {"resources": [15, 0, 0, 0],
               "buildings": [{"type": "kEnduranceStatue", "state": "kNormal"}],
               "blueprints": [blueprint("kCaptainQuarters", cost=(99, 0, 0, 0))],
               "build_options": [dict(blueprint("kCaptainQuarters", cost=(20, 0, 0, 0)), can_build_more=True)]}
        row = self.recommendations(raw)[0]
        self.assertEqual(row.cost, (20, 0, 0, 0))
        self.assertEqual(row.shortfall, 5)
        self.assertEqual(row.priority_group, "guide_core")
        self.assertIn("확인 필요", row.reason)
        self.assertEqual(row.desc, data.buildings["captainquarters"]["desc_ko"].replace("?", "N"))
        self.assertNotIn("Steam", row.desc)

    def test_identical_upgrades_share_one_row_but_different_costs_survive_limit(self):
        repeated = {"type": "kGrandTree", "lvl": 1, "state": "kNormal", "can_upgrade": True,
                    "upgrade_cost": [10, 0, 0, 0]}
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [dict(repeated) for _ in range(8)] +
                            [dict(repeated, lvl=2, upgrade_cost=[20, 0, 0, 0])],
               "blueprints": [blueprint("kBank")]}
        before = copy.deepcopy(raw)
        data = game_data()
        rows = suggest(parse_meta(raw, data), data, limit=3)
        self.assertEqual({(s.kind, s.type, s.cost) for s in rows},
                         {("upgrade", "kGrandTree", (10, 0, 0, 0)),
                          ("upgrade", "kGrandTree", (20, 0, 0, 0)),
                          ("build", "kBank", (10, 0, 0, 0))})
        self.assertEqual(len(rows), 3)
        self.assertEqual(raw, before)


if __name__ == "__main__":
    unittest.main()
