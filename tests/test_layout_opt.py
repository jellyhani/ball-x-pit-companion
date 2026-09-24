"""전체 재배치 최적화 (실제 기지 1개, tests/fixtures/harvest_trace_48deg.json)."""
import json
import os
import random
import unittest

from src.engine import layout_opt as lo
from tests import HAS_GAME_DATA
from src.engine.layout import grid_from_geo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


def setup():
    with open(FIX, encoding="utf-8") as f:
        fx = json.load(f)
    base = {"buildings": fx["buildings_before"], "geo": fx["geo"]}
    grid = grid_from_geo(fx["geo"])
    pieces, o0 = lo.pieces_from_base(base, grid, lo.housing_types())
    return fx, base, grid, pieces, o0


class LayoutOptTest(unittest.TestCase):
    @unittest.skipUnless(HAS_GAME_DATA, "게임 문구 없음 — tools/setup_data.py")
    def test_housing_from_game_text(self):
        h = lo.housing_types()
        self.assertIn("villa", h)
        self.assertIn("sheriffoffice", h)
        self.assertNotIn("forest", h)

    def test_region_swap_keeps_layout_valid(self):
        _, _, grid, pieces, o0 = setup()
        lay = lo.Layout(grid, pieces, o0)
        rng = random.Random(0)
        ids = [i for i, p in pieces.items() if p.movable]
        for _ in range(2000):
            i = rng.choice(ids)
            p = pieces[i]
            lay.swap_regions(lay.origin[i], rng.choice(sorted(grid.tiles)), p.w, p.h)
        cells = [c for i in lay.origin for c in lay.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))              # 겹침 없음
        self.assertTrue(set(cells) <= grid.tiles)                  # 산 땅 안

    def test_move_sequence_reaches_target_without_overlap(self):
        _, _, grid, pieces, o0 = setup()
        lay = lo.Layout(grid, pieces, o0)
        rng = random.Random(1)
        ids = [i for i, p in pieces.items() if p.movable]
        for _ in range(3000):
            i = rng.choice(ids)
            lay.swap_regions(lay.origin[i], rng.choice(sorted(grid.tiles)), pieces[i].w, pieces[i].h)
        target = dict(lay.origin)
        steps = lo.move_sequence(grid, pieces, o0, target)
        cur = lo.Layout(grid, pieces, o0)
        for i, o, _park in steps:
            others = {c for j in cur.origin if j != i for c in cur.cells(j)}
            self.assertFalse(set(cur.cells(i, o)) & others)         # 옮길 때마다 빈 자리로만
            self.assertTrue(set(cur.cells(i, o)) <= grid.tiles)
            cur.apply([(i, o)])
        self.assertEqual(cur.origin, target)

    def test_canonicalize_drops_same_type_swaps(self):
        _, _, grid, pieces, o0 = setup()
        forests = [i for i, p in pieces.items() if p.type == "kForest"]
        a, b = forests[:2]
        final = dict(o0)
        final[a], final[b] = o0[b], o0[a]
        self.assertEqual(lo.canonicalize(pieces, o0, final), o0)

    def test_optimize_never_worse(self):
        fx, base, *_ = setup()
        plan = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=3)
        self.assertGreaterEqual(plan.effect_after, plan.effect_before)
        self.assertEqual(plan.moved, sum(1 for i in plan.origin_after if plan.origin_after[i] != plan.origin_before[i]))

    def test_range_is_square_like_the_game(self):
        """게임의 범위 표시는 건물 중심 기준 사각형 (2×2 채석장, 범위 2.25 → 둘레 한 칸 4×4). 모서리 칸도 범위 안."""
        self.assertTrue(lo.in_range(1.6875, 1.6875, 2.25))       # 대각선 모서리 칸 (원이면 2.39 > 2.25 로 빠짐)
        self.assertFalse(lo.in_range(2.8125, 0.5625, 2.25))      # 한 칸 더 바깥

    def test_stones_go_back_into_quarry_box(self):
        """사용자 스크린샷: 채석장 네모 안이 비고 돌이 밖에 있으면, 최적 배치는 돌을 네모 안으로 되돌린다."""
        fx, base, grid, pieces, o0 = setup()
        lay = lo.Layout(grid, pieces, o0)
        mine = next(i for i, p in pieces.items() if p.type == "kIdleStoneMine")
        sc = lo.Scorer(pieces, lo.STAT_FALLBACK, lo.housing_types())
        before = sc.score(lay)[1]["kIdleStoneMine"]
        mc, mr = o0[mine]
        near = [i for i, p in pieces.items() if p.type == "kBoulder"
                and abs(o0[i][0] - mc - 0.5) <= 1.5 and abs(o0[i][1] - mr - 0.5) <= 1.5]
        far = sorted((c for c in grid.tiles if c not in lay.occ and abs(c[0] - mc) > 4), reverse=True)
        lay.apply([(i, far[k]) for k, i in enumerate(near[:4])])
        plan0 = lo.FullPlan(o0, dict(lay.origin), {i: lay.center(i) for i in lay.origin}, 0, 0, {}, {})
        moved_base = lo.final_base(base, plan0)          # 건물 위치 + 충돌 모양을 함께 옮긴 기지
        worse = sc.score(lay)[1]["kIdleStoneMine"]
        self.assertLess(worse, before)
        plan = lo.optimize(moved_base, None, None, seconds=3, restarts=2, seed=1)
        self.assertGreaterEqual(plan.detail_after["kIdleStoneMine"], before)

    def test_suggest_buying_stones_for_empty_quarry_slots(self):
        """채석장 둘레에서 돌을 빼 빈칸을 만들면 '바위 더 사기'를 권하고, 놓는 자리는 채석장 네모 안이다."""
        fx, base, grid, pieces, o0 = setup()
        mine = next(i for i, p in pieces.items() if p.type == "kIdleStoneMine")
        mc, mr = o0[mine]
        near = [i for i, p in pieces.items() if p.type == "kBoulder"
                and abs(o0[i][0] - mc - 0.5) <= 1.5 and abs(o0[i][1] - mr - 0.5) <= 1.5][:3]
        cut = dict(base, buildings=[b for b in base["buildings"] if b["id"] not in near])
        sug = {t: (c, n) for t, c, _sz, _g, n, _m in lo.suggest_tiles(cut)}
        self.assertIn("kBoulder", sug)
        self.assertGreaterEqual(sug["kBoulder"][1], 3)
        cx, cy = sug["kBoulder"][0]
        mx, my = grid.center(mc, mr, 2, 2)
        self.assertTrue(lo.in_range(cx - mx, cy - my, pieces[mine].range))

    @unittest.skipUnless(HAS_GAME_DATA, "게임 문구 없음 — tools/setup_data.py")
    def test_house_effect_follows_character_level(self):
        """거처 효과는 사는 캐릭터가 레벨 4(게임 값 3)일 때 켜진다 — 게임 문구 '○○의 거처'로 캐릭터를 찾는다."""
        self.assertEqual(lo.house_characters()["rockyhill"][0], "sisyphus")
        self.assertEqual(lo.house_characters()["villa"][0], "cogitator")
        fx, base, grid, *_ = setup()
        try:
            lo.set_char_levels({"kCogitator": 2})
            villa = next(p for p in lo.pieces_from_base(base, grid, lo.housing_types())[0].values() if p.type == "kVilla")
            self.assertEqual(villa.factor, lo.HOUSE_INACTIVE)
            lo.set_char_levels({"kCogitator": 3})
            villa = next(p for p in lo.pieces_from_base(base, grid, lo.housing_types())[0].values() if p.type == "kVilla")
            self.assertEqual(villa.factor, 1.0)
        finally:
            lo.set_char_levels({})

    def test_veteran_hut_bonus_grows_with_level(self):
        """잔병의 오두막 경험치 20·25·30% (화면 레벨 4·7·9, 위키) — 게임 레벨은 0부터."""
        self.assertEqual(lo._house_factor("kVeteranHut", 2), lo.HOUSE_INACTIVE)
        self.assertAlmostEqual(lo._house_factor("kVeteranHut", 3), 20 / 30)
        self.assertAlmostEqual(lo._house_factor("kVeteranHut", 6), 25 / 30)
        self.assertEqual(lo._house_factor("kVeteranHut", 8), 1.0)
        self.assertEqual(lo._house_factor("kVilla", 3), 1.0)
        self.assertEqual(lo._next_house_level("kVeteranHut", 3), 7)
        self.assertEqual(lo._next_house_level("kVilla", 1), 4)

    def test_iron_fortress_prefers_scaffolds(self):
        """강철 요새는 근처 공사장에 건설 점수 — 공사 중인 건물은 무한 강화 건물보다 두 배, 공사 중이어도 옮길 수 있다."""
        _, _, grid, _, _ = setup()
        sq = frozenset((x, y) for x in range(2) for y in range(2))
        fort = lo.Piece(1, "kBrickHouse", 2, 2, sq, True, 3.0)
        statue = lo.Piece(2, "kStrengthStatue", 2, 2, sq, True, 0.0)
        scaf = lo.Piece(3, "kIdleFarm", 2, 2, sq, True, 0.0, unfinished=True)
        pieces = {1: fort, 2: statue, 3: scaf}
        tiles = sorted(grid.tiles)
        x0, y0 = tiles[0]
        scorer = lo.Scorer(pieces, set(), set())
        near_scaf = lo.Layout(grid, pieces, {1: (x0, y0), 3: (x0 + 2, y0), 2: (x0 + 20, y0)})
        near_statue = lo.Layout(grid, pieces, {1: (x0, y0), 2: (x0 + 2, y0), 3: (x0 + 20, y0)})
        self.assertGreater(scorer.score(near_statue)[0], 0)
        self.assertAlmostEqual(scorer.score(near_scaf)[0], 2 * scorer.score(near_statue)[0])

    def test_previous_target_is_kept(self):
        fx, base, *_ = setup()
        p1 = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=4)
        p2 = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=9, prefer=p1.centers_after)
        if p1.moved:
            self.assertTrue(p2.origin_after == p1.origin_after or p2.effect_after >= p1.effect_after * 1.02,
                            (p1.effect_after, p2.effect_after, p2.notes))


if __name__ == "__main__":
    unittest.main()
