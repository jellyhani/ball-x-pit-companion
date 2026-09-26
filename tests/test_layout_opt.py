"""전체 재배치 최적화 (실제 기지 1개, tests/fixtures/harvest_trace_48deg.json)."""
import json
import os
import random
import unittest

from src.engine import layout_opt as lo
from tests import HAS_GAME_DATA
from src.engine.layout import grid_from_geo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


def shaped(pieces, plan):
    """계획의 회전(가로↔세로)을 반영한 건물 모양."""
    return {i: lo.rotated(p, plan.turned.get(i, 0)) for i, p in pieces.items()}


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

    @staticmethod
    def full_score(base, origin):
        """배치를 고르는 기준 (범위 효과 + 발사대 앞 구역). 보고하는 effect_after 는 범위 효과만."""
        grid = grid_from_geo(base["geo"])
        pieces, _ = lo.pieces_from_base(base, grid, lo.housing_types())
        sc = lo.Scorer(pieces, lo._stat_types(base), lo.housing_types(), lane=lo.lane_values(base["geo"], grid))
        return sc.score(lo.Layout(grid, pieces, origin))[0]

    def test_optimize_never_worse(self):
        fx, base, *_ = setup()
        plan = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=3)
        self.assertGreaterEqual(self.full_score(base, plan.origin_after), self.full_score(base, plan.origin_before) - 1e-9)
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
        # 채석장 둘레 채우기만 본다 — 발사대 앞 구역 점수는 채석장을 통째로 뒤로 빼는 배치를 고를 수 있어 끈다
        keep = (lo.LANE_W, lo.LANE_TILE_W)
        lo.LANE_W = lo.LANE_TILE_W = 0.0
        try:
            plan = lo.optimize(moved_base, None, None, seconds=6, restarts=2, seed=1)   # 시간 제한 탐색 — 전체 테스트 부하에서도 찾게
        finally:
            lo.LANE_W, lo.LANE_TILE_W = keep
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

    def test_front_lane_penalizes_idle_buildings_only(self):
        """발사대 앞: 능력치 건물은 벌점, 자원 타일·금광·공사 중인 건물은 벌점 없음."""
        fx, _, grid, _, _ = setup()
        lane = lo.lane_values(fx["geo"], grid)
        near = max(lane, key=lane.get)
        sq = frozenset((x, y) for x in range(2) for y in range(2))
        for typ, unfinished, penalized in (("kStrengthStatue", False, True), ("kForest", False, False),
                                           ("kGoldMine", False, False), ("kStrengthStatue", True, False)):
            pcs = {1: lo.Piece(1, typ, 2, 2, sq, True, 0.0, unfinished=unfinished)}
            sc = lo.Scorer(pcs, set(), set(), lane=lane)
            got = sc.score(lo.Layout(grid, pcs, {1: near}))[0]
            self.assertEqual(got < 0, penalized, typ)

    def test_previous_target_is_kept(self):
        fx, base, *_ = setup()
        p1 = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=4)
        p2 = lo.optimize(base, None, None, seconds=1.5, restarts=1, seed=9, prefer=p1.centers_after)
        if p1.moved:
            s1, s2 = self.full_score(base, p1.origin_after), self.full_score(base, p2.origin_after)
            self.assertTrue(p2.origin_after == p1.origin_after or s2 > s1, (s1, s2, p2.notes))

    def test_suggest_demolish_flags_isolated_idle_building(self):
        """근처에 캘 바위가 없는 채석장은 철거 후보 — 실제 채석장(id 49)과 같은 range 로 아주 먼 자리에 하나 더 놓는다."""
        fx, base, grid, pieces, o0 = setup()
        new_id = max(b["id"] for b in base["buildings"]) + 1000
        far = grid.center(1000, 1000, 2, 2)
        far_bld = dict(id=new_id, type="kIdleStoneMine", x=far[0], y=far[1], tw=2, th=2, rot=0, range=2.25)
        base2 = dict(base, buildings=base["buildings"] + [far_bld])
        out = lo.suggest_demolish(base2)
        self.assertIn(new_id, [i for i, *_ in out])
        for i, t, score, why in out:
            self.assertIn(t, lo.DEMOLISH_CANDIDATE_TYPES | set(lo.GUIDE_DEMOLISH))
            self.assertLess(score, lo.DEMOLISH_MAX_SCORE)
            self.assertTrue(why)

    def test_suggest_demolish_never_flags_excluded_types(self):
        """금광·거처·능력치 건물은 Scorer 가 가치를 제대로 모르므로(또는 보통 하나뿐이라) 절대 후보에 안 나온다."""
        _, base, *_ = setup()
        out = lo.suggest_demolish(base)
        types = {t for _, t, *_ in out}
        self.assertFalse(types & {"kVeteranHut", "kMansion", "kSingleFamilyHome"})

    def test_guide_demolish_candidates(self):
        """전쟁 회의실·채집가의 오두막(Steam 가이드)·금광(사용자 결정: 골드 충분)은 있기만 하면 철거 후보, 맨 위."""
        fx, base, grid, pieces, o0 = setup()
        extra = []
        for k, t in enumerate(("kWarRoom", "kGoldMine")):
            x, y = grid.center(8 + 2 * k, 0, 2, 2)
            extra.append(dict(id=950 + k, type=t, x=x, y=y, tw=2, th=2, rot=0, range=0.0))
        out = lo.suggest_demolish(dict(base, buildings=base["buildings"] + extra))
        self.assertEqual({t for _, t, *_ in out[:2]}, {"kWarRoom", "kGoldMine"})
        self.assertIn("가이드", out[0][3] + out[1][3])

    def test_plan_preset_builds_production_units(self):
        """계획도시: 생산 건물마다 둘레를 자원 타일로 두른 유닛 — 채석장 둘레 바위 12개, 농장 둘레 밀밭 8개가 모두 범위 안."""
        fx, base, grid, pieces, o0 = setup()
        plan = lo.optimize(base, None, None, preset="plan")
        self.assertIsNotNone(plan)
        lay = lo.Layout(grid, shaped(pieces, plan), plan.origin_after)
        cells = [c for i in plan.origin_after for c in lay.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))                 # 겹침 없음
        self.assertTrue(set(cells) <= grid.tiles)                      # 산 땅 안
        self.assertEqual(set(plan.origin_after), set(pieces))          # 빠진 건물 없음
        for prod, kind, n in (("kIdleStoneMine", lo.STONE_T, 12), ("kIdleFarm", lo.WHEAT_T, 8)):
            e = next(i for i, p in pieces.items() if p.type == prod)
            ex, ey = lay.center(e)
            near = [i for i, p in pieces.items() if p.type in kind
                    and lo.in_range(lay.center(i)[0] - ex, lay.center(i)[1] - ey, pieces[e].range)]
            self.assertEqual(len(near), n, prod)

    def test_plan_preset_packs_town_away_from_launcher(self):
        """가이드 배치: 마을 건물은 발사대에서 먼 쪽에 빽빽하게 (테두리 사각형 안 빈 땅이 적게), 결과는 늘 같다."""
        from src.engine import layout_city as lc
        fx, base, grid, pieces, o0 = setup()
        plan = lo.optimize(base, None, None, preset="plan")
        again = lo.optimize(base, None, None, preset="plan")
        self.assertEqual(plan.origin_after, again.origin_after)
        lay = lo.Layout(grid, shaped(pieces, plan), plan.origin_after)
        # 마을 = 생산 구역에 가는 것(자원 타일·생산 건물·채집 거처·발사대 쪽 건물·금광)과 잔병의 오두막을 뺀 나머지
        away = lc._harvest_houses() | lc.FRONT_TYPES | lo.STATUE_TYPES | {lc.VETERAN, "kGoldMine"}
        prod = {i for i, p in pieces.items() if p.type in lo.TILE_RES or p.type in lc.PRODUCERS}
        town = [i for i in pieces if i not in prod and pieces[i].movable and pieces[i].type not in away]
        tcells = {c for i in town for c in lay.cells(i)}
        mean = lambda ids: sum(lay.center(i)[1] for i in ids) / len(ids)
        self.assertGreater(mean(town), mean(prod))                     # 발사대는 아래(행 0 쪽)
        # 테두리 사각형 안의 '빈 땅'만 센다 — 채집 거처 옆에 일부러 둔 자원 타일은 빈틈이 아님
        occ = {c for i in plan.origin_after for c in lay.cells(i)}
        cs, rs = [c for c, _ in tcells], [r for _, r in tcells]
        rect = {(c, r) for c in range(min(cs), max(cs) + 1) for r in range(min(rs), max(rs) + 1)} & grid.tiles
        self.assertLess(len(rect - occ), len(tcells) * 0.15)

    def test_guide_rules_on_real_base(self):
        """가이드(Steam Zarcos·apo·Drake): 거처는 전부 잔병의 오두막 범위 안, 채집·재생 거처는 자기 자원 타일을 범위에,
        건물이 빠지거나 겹치지 않는다. 메모에 달성도가 나온다."""
        from src.engine import layout_city as lc
        fx, base, grid, pieces, o0 = setup()
        plan = lo.optimize(base, None, None, preset="plan")
        shp = shaped(pieces, plan)
        lay = lo.Layout(grid, shp, plan.origin_after)
        cells = [c for i in plan.origin_after for c in lay.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(set(plan.origin_after), set(pieces))
        vet = next(p for p in pieces.values() if p.type == lc.VETERAN)
        homes = [i for i, p in pieces.items() if lo._slug(p.type) in lo.housing_types() and i != vet.id]
        self.assertTrue(homes)
        pad = 1.125                                                   # 이 기지 범위 여유 (calibrate_range 값과 같은 크기)
        rng = (vet.range + pad) / grid.size
        vx, vy = lay.center(vet.id)
        far = [pieces[i].type for i in homes if abs(lay.center(i)[0] - vx) > rng or abs(lay.center(i)[1] - vy) > rng]
        self.assertLessEqual(len(far), 1, far)                         # 거의 전부 (범위 여유에 따라 하나쯤)
        self.assertTrue(any("잔병의 오두막 범위 안 거처" in n for n in plan.notes))
        self.assertTrue(any("채집·재생 거처" in n for n in plan.notes))

    def test_guide_plan_keeps_layout_when_already_done(self):
        """가이드 배치를 적용한 뒤 다시 계산하면 옮길 게 없다 (지금 배치가 가이드 기준으로 같거나 나음 → 그대로)."""
        fx, base, grid, pieces, o0 = setup()
        plan = lo.optimize(base, None, None, preset="plan")
        fb = lo.final_base(base, plan)
        again = lo.optimize(fb, None, None, preset="plan")
        self.assertEqual(again.moved, 0)
        self.assertTrue(any("지금 배치가 가이드 기준으로" in n for n in again.notes))

    def test_stat_types_ignores_knum(self):
        """게임은 '능력치 없음'을 kNum 으로 보낸다 — 능력치 건물로 세면 안 됨 (대위 막사 대상)."""
        base = {"buildings": [{"type": "kForest", "stat": "kNum"}, {"type": "kClinic", "stat": "kEndurance"},
                              {"type": "kGunsmith", "stat": "kDexterity"}]}
        self.assertEqual(lo._stat_types(base), {"kClinic", "kGunsmith"})

    def test_plan_puts_unused_gold_mines_at_the_back(self):
        """계획도시: 금광은 쓰지 않으니(사용자 결정) U자 자리를 비워 두지 않고 발사대에서 먼 쪽에 모아 둔다."""
        fx, base, grid, *_ = setup()
        extra = []
        for k, c in enumerate((8, 10, 12)):
            x, y = grid.center(c, 0, 2, 2)
            extra.append(dict(id=900 + k, type="kGoldMine", x=x, y=y, tw=2, th=2, rot=0, range=0.0))
        base2 = dict(base, buildings=base["buildings"] + extra)
        pieces, _ = lo.pieces_from_base(base2, grid, lo.housing_types())
        plan = lo.optimize(base2, None, None, preset="plan")
        spots = set(lo.gold_u_spots(base2["geo"], grid))
        mines = [plan.origin_after[900 + k] for k in range(3)]
        self.assertFalse(spots & set(mines))                          # U자 자리가 아님
        lay = lo.Layout(grid, shaped(pieces, plan), plan.origin_after)
        rows = [r for _, r in grid.tiles]
        mean_all = sum(lay.center(i)[1] for i in plan.origin_after) / len(plan.origin_after)
        self.assertGreater(sum(lay.center(900 + k)[1] for k in range(3)) / 3, mean_all)   # 발사대(아래)에서 먼 쪽
        self.assertTrue(any("금광" in n for n in plan.notes))

    def test_rotated_mask_matches_rotated_collider(self):
        """ㄱ·ㅜ 자 건물: 충돌 모양을 시계 방향으로 돌린 게임 자료에서 읽은 모양 == 칸 모양을 돌린 것 (rot +1 = 시계 90°)."""
        fx, base, grid, pieces, o0 = setup()
        odd = [i for i, p in pieces.items() if len(p.rel) < p.w * p.h and p.w != p.h][:3]
        self.assertTrue(odd)
        for k in (1, 2, 3):
            turn = {i: k for i in odd}
            shaped = {i: lo.rotated(p, turn.get(i, 0)) for i, p in pieces.items()}
            lay = lo.Layout(grid, shaped, o0)
            plan = lo.FullPlan(o0, dict(o0), {i: lay.center(i) for i in o0}, 0, 0, {}, {}, turned=turn)
            fb = lo.final_base(base, plan)
            got, _ = lo.pieces_from_base(fb, grid, lo.housing_types())
            for i in odd:
                self.assertEqual((got[i].w, got[i].h, got[i].rel), (shaped[i].w, shaped[i].h, shaped[i].rel), (pieces[i].type, k))

    def test_plan_turns_buildings_and_moves_reach_it(self):
        """계획도시는 건물을 회전(가로↔세로)해 넣기도 한다. 목표 배치를 게임에 적용하면(rot 바뀜) 남은 옮기기가 0,
        지금 배치에서 가는 옮기기 순서는 겹침 없이 목표에 닿는다."""
        fx, base, grid, pieces, o0 = setup()
        plan = lo.optimize(base, None, None, preset="plan")
        self.assertTrue(plan.turned)                                   # 이 기지에서는 회전해야 더 빽빽함
        fb = lo.final_base(base, plan)
        rots = {b["id"]: b.get("rot", 0) for b in fb["buildings"]}
        for i, k in plan.turned.items():
            before = next(b for b in base["buildings"] if b["id"] == i).get("rot", 0)
            self.assertEqual((rots[i] - before) % 4, k)
        final = {b["id"]: (b["x"], b["y"]) for b in fb["buildings"]}
        self.assertEqual(lo.remaining_moves(fb, final, rots), [])
        moves = lo.remaining_moves(base, final, rots)
        self.assertTrue(any(m.rot >= 0 for m in moves))
        # 순서대로 옮기면(회전 포함) 매 단계 겹치지 않는다
        turn = dict(plan.turned)
        cur = dict(o0)
        live = dict(pieces)
        for i, o, park in lo.move_sequence(grid, pieces, o0, plan.origin_after, turn):
            if i in turn and not park:
                live[i] = lo.rotated(pieces[i], turn[i])
            cur[i] = o
            cells = [c for j in cur for c in lo.Layout(grid, live, cur).cells(j)]
            self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(cur, {**o0, **plan.origin_after})


if __name__ == "__main__":
    unittest.main()
