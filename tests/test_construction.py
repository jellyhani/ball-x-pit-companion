"""건설 목록은 실제 해금·크기·비용을 쓰고, 고급 자원 타일을 빠뜨리지 않는다."""
import copy
import unittest

from src.engine import layout_opt as lo, sim_jobs
from src.engine.base_advisor import suggest
from src.engine.construction_policy import preferred_tile_types
from src.engine.layout import grid_from_geo, buildings_from_base, moved_base
from src.tracking.meta_state import parse_meta
from tests.helpers import game_data


def option(kind, size=(1, 1), cost=(100, 1, 0, 0)):
    return {"type": kind, "size": size, "cost": cost, "can_build_more": True}


def empty_farm():
    return {"geo": {"space_w": 1.0, "chunk_w": 8, "chunk_h": 8, "chunks": [[0, 0]],
                    "left": 0.0, "bottom": 0.0, "colliders": []},
            "buildings": [{"id": 1, "type": "kIdleFarm", "x": 3.0, "y": 3.0, "tw": 2, "th": 2,
                           "range": 2.5, "rot": 0, "worker": 0, "lvl": 0, "state": "kNormal",
                           "stat": "kNum", "in_range": {"kWheatField": 0, "kDenseWheat": 0}}]}


class ConstructionCatalogTest(unittest.TestCase):
    def test_repeatable_owned_type_survives_new_catalog(self):
        raw = {"buildings": [{"type": "kDenseWheat", "lvl": 0}], "blueprints": [],
               "build_options": [{"type": "kDenseWheat", "tw": 1, "th": 1, "cost": [107, 2, 0, 0],
                                  "can_build_more": True},
                                 {"type": "kWheatField", "tw": 2, "th": 2, "cost": [30, 0, 0, 0],
                                  "can_build_more": False}]}
        meta = parse_meta(raw, game_data())
        self.assertEqual(meta.blueprints, [])
        self.assertEqual([b.type for b in meta.build_options], ["kDenseWheat"])
        self.assertEqual(meta.build_options[0].construction_data()["cost"], (107, 2, 0, 0))
        self.assertEqual(meta.build_options[0].size, (1, 1))
        self.assertIsNone(parse_meta({}, game_data()).build_options)
        self.assertEqual(parse_meta({"build_options": []}, game_data()).build_options, [])

    def test_old_incomplete_blueprints_do_not_recommend_basic_wheat(self):
        raw = {"resources": [1000, 100, 100, 100], "buildings": [{"type": "kDenseWheat", "lvl": 0}],
               "blueprints": [{"type": "kWheatField", "tw": 2, "th": 2, "cost": [30, 0, 0, 0]}]}
        self.assertNotIn("kWheatField", [s.type for s in suggest(parse_meta(raw, game_data()), game_data())])

    def test_advanced_unlock_replaces_lower_upgrade_in_advice(self):
        raw = {"resources": [1000, 100, 100, 100],
               "buildings": [{"type": "kWheatField", "lvl": 0, "state": "kNormal", "can_upgrade": True,
                              "upgrade_cost": [120, 0, 0, 0]}],
               "blueprints": [{"type": "kDenseWheat", "tw": 1, "th": 1, "cost": [100, 1, 0, 0]},
                              {"type": "kWheatField", "tw": 2, "th": 2, "cost": [30, 0, 0, 0]}]}
        result = suggest(parse_meta(raw, game_data()), game_data())
        self.assertIn(("build", "kDenseWheat"), [(s.kind, s.type) for s in result])
        self.assertNotIn("kWheatField", [s.type for s in result])

    def test_tiers_are_consistent_for_all_three_resources(self):
        all_types = {"kWheatField", "kDenseWheat", "kForest", "kGrandTree", "kBoulder", "kGraniteSlab"}
        self.assertEqual(preferred_tile_types(all_types), {"kDenseWheat", "kGrandTree", "kGraniteSlab"})
        self.assertEqual(preferred_tile_types({"kWheatField"}), {"kWheatField"})


class TileConstructionTest(unittest.TestCase):
    def test_purchase_count_respects_game_instance_limit(self):
        limited=dict(option('kDenseWheat'),max_instances=1,range_boxes=[[-.5,-.5,.5,.5]])
        result=lo.suggest_tiles(empty_farm(),build_options=[limited],resources=(10000,1000,0,0))
        self.assertEqual(result[0][4],1)
        limited['max_instances']=0
        self.assertEqual(lo.suggest_tiles(empty_farm(),build_options=[limited]),[])

    def test_meta_preserves_instance_limit_and_shape(self):
        meta=parse_meta({'build_options':[dict(type='kDenseWheat',tw=1,th=1,can_build_more=True,
                                              max_instances=3,range_boxes=[[-.5,-.5,.5,.5]])]},game_data())
        data=meta.build_options[0].construction_data()
        self.assertEqual(data['max_instances'],3)
        self.assertEqual(data['range_boxes'],((-.5,-.5,.5,.5),))

    def test_game_range_disagreement_defers_layout(self):
        base=empty_farm();base['range_contract']={'mismatches':[[1,2,True,False]]}
        plan,angles=sim_jobs.job_layout(base,[],1,[])
        self.assertTrue(plan.calculation_deferred)
        self.assertFalse(plan.swaps)
        self.assertEqual(angles,{})

    def test_dense_wheat_uses_game_size_cost_and_budget(self):
        base = empty_farm()
        original = copy.deepcopy(base)
        options = [option("kWheatField", (2, 2), (30, 0, 0, 0)), option("kDenseWheat"),
                   option("kGoldMine", (2, 2), (0, 0, 10, 12))]
        result = lo.suggest_tiles(base, build_options=options, resources=(250, 3, 0, 0))
        self.assertEqual([r[0] for r in result], ["kDenseWheat"])
        kind, center, size, _, count, moved = result[0]
        self.assertEqual((size, count, moved), ((1, 1), 2, 0))
        grid = grid_from_geo(base["geo"])
        self.assertFalse(grid.cells(*center, *size) & grid.cells(3, 3, 2, 2))
        self.assertEqual(base, original)

    def test_unaffordable_advanced_does_not_fall_back_to_basic(self):
        options = [option("kWheatField", (2, 2), (30, 0, 0, 0)), option("kDenseWheat")]
        self.assertEqual(lo.suggest_tiles(empty_farm(), build_options=options, resources=(30, 0, 0, 0)), [])

    def test_missing_game_catalog_or_range_does_not_invent_purchase(self):
        self.assertEqual(lo.suggest_tiles(empty_farm()), [])
        base = empty_farm()
        base["buildings"][0].pop("in_range")
        self.assertEqual(lo.suggest_tiles(base, build_options=[option("kDenseWheat")]), [])

    def test_entrance_is_not_a_purchase_site(self):
        base = empty_farm()
        base["geo"]["entrance_chunk"] = [0, 0]
        grid = grid_from_geo(base["geo"])
        result = lo.suggest_tiles(base, max_each=1, build_options=[option("kDenseWheat")])
        self.assertTrue(result)
        self.assertFalse(grid.cells(*result[0][1], *result[0][2]) & lo.entrance_cells(base["geo"], grid))

    def test_missing_new_building_range_is_not_guessed(self):
        self.assertEqual(lo.suggest_builds(empty_farm(), [option("kIdleStoneMine", (2, 2))]), [])

    def test_new_building_gain_does_not_include_unlisted_rearrangement(self):
        base = empty_farm()
        base["buildings"].append({"id": 2, "type": "kDenseWheat", "x": 6.5, "y": 3.5,
                                  "tw": 1, "th": 1, "range": 0, "cap": 4, "lvl": 0, "stat": "kNum"})
        original = copy.deepcopy(base)
        result = lo.suggest_builds(base, [option("kIdleFarm", (2, 2))])
        self.assertTrue(result)
        self.assertTrue(all(row[5] == 0 for row in result))
        self.assertEqual(base, original)

    def test_existing_unfinished_or_unmanned_producer_is_used_first(self):
        for state, worker in (("kScaffold", 0), ("kNormal", -1)):
            base = empty_farm()
            base["buildings"][0].update(state=state, worker=worker)
            base["buildings"].append({"id": 2, "type": "kDenseWheat", "x": 6.5, "y": 3.5,
                                      "tw": 1, "th": 1, "range": 0, "cap": 4, "lvl": 0, "stat": "kNum"})
            self.assertEqual(lo.suggest_builds(base, [option("kIdleFarm", (2, 2))]), [])

    def test_new_producer_count_does_not_include_existing_producer(self):
        base = empty_farm()
        for i, x in ((2, 1.5), (3, 6.5)):
            base["buildings"].append({"id": i, "type": "kDenseWheat", "x": x, "y": 3.5,
                                      "tw": 1, "th": 1, "range": 0, "cap": 4, "lvl": 0, "stat": "kNum"})
        result = lo.suggest_builds(base, [option("kIdleFarm", (2, 2))])
        self.assertTrue(result)
        x, y = result[0][1]
        expected = sum(lo.in_range(b["x"] - x, b["y"] - y, 2.5)
                       for b in base["buildings"] if b["type"] == "kDenseWheat")
        self.assertEqual(result[0][4], expected)

    def test_construction_waits_for_unapplied_layout(self):
        base = empty_farm()
        grid = grid_from_geo(base["geo"])
        parts, before = lo.pieces_from_base(base, grid, lo.housing_types())
        after = {1: (4, 2)}
        full = lo.FullPlan(before, after, {1: grid.center(4, 2, 2, 2)}, 0, 0, {}, {})
        plan = sim_jobs._plan_from(base, full, grid, {}, [], lambda g: {}, lambda g: [0, 0, 0, 0],
                                  [option("kDenseWheat")], {}, 0.0, {}, 16.0, 1, (0, 0, 0), (1000, 20, 0, 0))
        self.assertTrue(plan.construction_pending)
        self.assertEqual(plan.builds, [])
        self.assertEqual(plan.new_spots, [])

    def test_unfinished_gold_mine_is_not_a_layout_construction_target(self):
        base = empty_farm()
        base["buildings"].append({"id": 2, "type": "kGoldMine", "x": 6, "y": 6, "tw": 2, "th": 2,
                                  "range": 0, "state": "kScaffold", "lvl": 0, "stat": "kNum"})
        parts, _ = lo.pieces_from_base(base, grid_from_geo(base["geo"]), lo.housing_types())
        self.assertFalse(parts[2].unfinished)
        self.assertEqual(base["buildings"][-1]["state"], "kScaffold")

    def test_construction_access_must_not_sacrifice_active_farm(self):
        base = empty_farm()
        base["buildings"][0]["in_range"] = {"kDenseWheat": 1}
        base["buildings"].append({"id": 2, "type": "kDenseWheat", "x": 1.5, "y": 3.5,
                                  "tw": 1, "th": 1, "range": 0, "cap": 4, "stat": "kNum"})
        self.assertTrue(lo.preserves_production(base, copy.deepcopy(base)))
        moved = moved_base(base, buildings_from_base(base), 1, (6, 6))
        self.assertFalse(lo.preserves_production(base, moved))
        lower_capacity = copy.deepcopy(base)
        lower_capacity["buildings"][1]["cap"] = 1
        self.assertFalse(lo.preserves_production(base, lower_capacity))


if __name__ == "__main__":
    unittest.main()
