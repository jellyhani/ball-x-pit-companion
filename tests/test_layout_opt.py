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
        """가이드 배치: 생산 건물마다 둘레를 자원 타일로 두른 유닛 — 채석장 둘레 바위 12개, 농장 둘레 밀밭(2×2) 8개가 모두 범위 안."""
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
        # 마을 테두리 사각형 안에서 바깥(들판)과 이어지지 않은 '갇힌 빈 땅'만 센다 — 아무것도 못 쓰는 낭비.
        # 들판 쪽 가장자리가 들쭉날쭉한 것은 들판 타일 배치에 따라 달라져서 세지 않는다.
        occ = {c for i in plan.origin_after for c in lay.cells(i)}
        cs, rs = [c for c, _ in tcells], [r for _, r in tcells]
        rect = {(c, r) for c in range(min(cs), max(cs) + 1) for r in range(min(rs), max(rs) + 1)} & grid.tiles
        empty = rect - occ
        seen = [c for c in empty if any((c[0] + dx, c[1] + dy) in grid.tiles - rect - occ
                                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        opened = set(seen)
        while seen:
            c, r = seen.pop()
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in empty and n not in opened:
                    opened.add(n)
                    seen.append(n)
        self.assertLess(len(empty - opened), len(tcells) * 0.15)

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


class GuideFewMovesTest(unittest.TestCase):
    """가이드 배치(preset guide): 지금 배치에서 출발해 허브 규칙을 고치고, 처음부터 다시 짜는 것보다 적게 옮긴다
    (사용자 2026-09-26: '적게 움직이고 효율 최대, 가이드대로')."""

    def test_repair_raises_coverage_without_overlap(self):
        from src.engine import layout_guide as lg
        fx, base, grid, pieces, o0 = setup()
        lane = lo.lane_values(base["geo"], grid)
        sc = lo.Scorer(pieces, lo._stat_types(base), lo.housing_types(), None, 0.0, lane=lane)
        groups = lg.hub_groups(pieces, sc)
        rep = lg.repair(grid, pieces, o0, groups, 0.0, lane)
        self.assertIsNotNone(rep)
        org, turn = rep
        shaped = {i: lo.rotated(p, turn.get(i, 0)) for i, p in pieces.items()}
        lay = lo.Layout(grid, shaped, org)
        cells = [c for i in org for c in lay.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertTrue(set(cells) <= grid.tiles)
        self.assertGreaterEqual(lg.coverage(grid, shaped, org, groups, 0.0), lg.coverage(grid, pieces, o0, groups, 0.0))

    def test_guide_clears_game_entrance_and_places_unfinished_forest_in_front(self):
        """게임이 지정한 입구를 비우고, 강철 요새와 무관한 강화 중 숲도 공이 지나는 곳에 둔다."""
        from src.engine import layout_guide as lg
        _, base, grid, _, _ = setup()
        base["geo"]["entrance_grid"] = [2, 0]  # 실행 중인 1.13.0 브리지와 같은 입구 청크 값
        entrance = lo.entrance_cells(base["geo"], grid)
        self.assertEqual(entrance, {(19, 0), (20, 0), (19, 1), (20, 1)})
        next(b for b in base["buildings"] if b["id"] == 39)["state"] = "kUpgrading"
        pieces, origin = lo.pieces_from_base(base, grid, lo.housing_types())
        lane = lo.lane_values(base["geo"], grid)
        self.assertFalse(any(lane.get(c, 0.0) > 0 for c in lo.Layout(grid, pieces, origin).cells(39)))
        sc = lo.Scorer(pieces, lo._stat_types(base), lo.housing_types(), lane=lane)
        rep = lg.repair(grid, pieces, origin, lg.hub_groups(pieces, sc), 0.0, lane, entrance)
        self.assertIsNotNone(rep)
        org, turn = rep
        lay = lo.Layout(grid, {i: lo.rotated(p, turn.get(i, 0)) for i, p in pieces.items()}, org)
        occupied = [c for i in org for c in lay.cells(i)]
        self.assertEqual(len(occupied), len(set(occupied)))
        self.assertTrue(set(occupied) <= grid.tiles)
        self.assertFalse(entrance & set(occupied))
        self.assertTrue(any(lane.get(c, 0.0) > 0 for c in lay.cells(39)))

        plan = lo.optimize(base, None, None, preset="guide", seconds=2.0)
        self.assertIsNotNone(plan)
        final = lo.Layout(grid, shaped(pieces, plan), plan.origin_after)
        self.assertFalse(entrance & {c for i in plan.origin_after for c in final.cells(i)})
        self.assertTrue(any(lane.get(c, 0.0) > 0 for c in final.cells(39)))

    def test_guide_ignores_unverified_entrance(self):
        """옛 연동값이나 잘못된 칸으로 입구를 추측하지 않는다."""
        _, base, grid, _, _ = setup()
        self.assertEqual(lo.entrance_cells(base["geo"], grid), set())
        base["geo"]["entrance_chunk"] = [100, 100]
        self.assertEqual(lo.entrance_cells(base["geo"], grid), set())
        base["geo"]["entrance_chunk"] = [2, 0]
        self.assertEqual(lo.entrance_cells(base["geo"], grid), {(19, 0), (20, 0), (19, 1), (20, 1)})

    def test_staffed_quarry_gets_free_coverage_move_below_global_cutoff(self):
        """채석장 바위 2→4개는 전체 효과 +4% 미만이어도 한 번 옮겨 챙긴다."""
        from src.engine.layout import buildings_from_base, moved_base
        _, base, grid, pieces, _ = setup()
        quarry = next(i for i, p in pieces.items() if p.type == "kIdleStoneMine")
        keep = {i for i, p in pieces.items() if p.type == "kIdleFarm" or
                (p.type in lo.TILE_RES and lo.TILE_RES[p.type] == 1)} | {quarry, 52, 53, 54, 55}
        base["buildings"] = [b for b in base["buildings"] if b["id"] in keep]
        base["geo"] = dict(base["geo"], colliders=[c for c in base["geo"]["colliders"] if c["id"] in keep])
        poor = moved_base(base, buildings_from_base(base), quarry, grid.center(21, 0, 2, 2))
        next(b for b in poor["buildings"] if b["id"] == quarry)["in_range"] = {"kBoulder": 2}
        before_pieces, before = lo.pieces_from_base(poor, grid, lo.housing_types())
        self.assertEqual(before_pieces[quarry].factor, 1.0)  # 일꾼 배정됨
        plan = lo.optimize(poor, None, None, preset="guide", seconds=2.0)
        self.assertIsNotNone(plan)

        def covered(origin):
            x, y = grid.center(*origin[quarry], 2, 2)
            return sum(lo.in_range(grid.center(*origin[i], p.w, p.h)[0] - x,
                                   grid.center(*origin[i], p.w, p.h)[1] - y, before_pieces[quarry].range)
                       for i, p in before_pieces.items() if p.type in lo.TILE_RES and lo.TILE_RES[p.type] == 3)

        self.assertEqual(covered(before), 2)
        self.assertGreaterEqual(covered(plan.origin_after), 4)
        self.assertLess((plan.effect_after / plan.effect_before - 1) * 100, 4)
        next(b for b in poor["buildings"] if b["id"] == quarry)["in_range"] = {}
        unverified = lo.optimize(poor, None, None, preset="guide", seconds=2.0)
        self.assertEqual(unverified.origin_after[quarry], before[quarry])

    def test_old_bank_target_without_real_gain_is_dropped(self):
        """지난 목표가 은행을 앞 구역에서만 빼고 범위·채집 효과를 못 올리면 헛걸음을 남기지 않는다."""
        from src.engine.layout import buildings_from_base, moved_base
        _, base, grid, pieces, _ = setup()
        bank = next(i for i, p in pieces.items() if p.type == "kWatchTower")
        keep = {i for i, p in pieces.items() if p.type == "kIdleFarm" or
                (p.type in lo.TILE_RES and lo.TILE_RES[p.type] == 1)} | {bank}
        base["buildings"] = [dict(b, type="kBank", stat="kNum") if b["id"] == bank else b
                             for b in base["buildings"] if b["id"] in keep]
        base["geo"] = dict(base["geo"], colliders=[c for c in base["geo"]["colliders"] if c["id"] in keep])
        parts, origin = lo.pieces_from_base(base, grid, lo.housing_types())
        lay = lo.Layout(grid, parts, origin)
        lane = lo.lane_values(base["geo"], grid)
        spots = []
        for at in grid.tiles:
            cells = {(at[0] + dx, at[1] + dy) for dx, dy in parts[bank].rel}
            if cells <= grid.tiles and all(lay.occ.get(c) in (None, bank) for c in cells):
                spots.append((sum(lane.get(c, 0.0) for c in cells), at))
        near, far = max(spots)[1], min(spots)[1]
        poor = moved_base(base, buildings_from_base(base), bank, grid.center(*near, 2, 2))
        previous_goal = {bank: grid.center(*far, 2, 2)}
        plan = lo.optimize(poor, None, None, preset="guide", seconds=2.0, prefer=previous_goal)
        self.assertEqual(plan.moved, 0)

    def test_guide_moves_less_than_full_rebuild(self):
        from src.engine import layout_guide as lg
        fx, base, grid, pieces, o0 = setup()
        guide = lo.optimize(base, None, None, preset="guide", seconds=3.0)
        rebuild = lo.optimize(base, None, None, preset="plan")
        self.assertLess(guide.moved, rebuild.moved)
        shaped = {i: lo.rotated(p, guide.turned.get(i, 0)) for i, p in pieces.items()}
        lay = lo.Layout(grid, shaped, guide.origin_after)
        cells = [c for i in guide.origin_after for c in lay.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))
        sc = lo.Scorer(pieces, lo._stat_types(base), lo.housing_types())
        groups = lg.hub_groups(pieces, sc)
        self.assertGreaterEqual(lg.coverage(grid, shaped, guide.origin_after, groups, 0.0),
                                lg.coverage(grid, pieces, o0, groups, 0.0))
        self.assertTrue(any("가이드 배치: 지금 배치에서 출발해" in n for n in guide.notes))


class ProductionUnitTest(unittest.TestCase):
    def test_unit_fills_every_ring_in_range(self):
        """농장(2×2, 범위 3칸)은 둘레 두 겹(6×6 = 밀밭 32개)이 범위 안 — 한 겹만 두르면 바깥 겹 범위가 버려진다
        (사용자 스크린샷 2026-09-26: 농장·채석장 아래 한 줄이 비고, 채석장 범위에 밀밭)."""
        from src.engine import layout_city as lc
        size = 1.125
        farm = lo.Piece(0, "kIdleFarm", 2, 2, frozenset({(0, 0), (1, 0), (0, 1), (1, 1)}), True, 3 * size, 1.0, 1.0, False)
        wheat = [lo.Piece(i, "kDenseWheat", 1, 1, frozenset({(0, 0)}), True, 0.0, 1.0, 1.0, False) for i in range(1, 41)]
        it, rest = lc._unit(farm, wheat, 0.0, False, size)
        self.assertEqual((it.w, it.h), (6, 6))
        self.assertEqual(len(it.members) - 1, 32)
        self.assertEqual(len(rest), 8)
        self.assertEqual(it.members[0][1], (2, 2))                    # 농장은 한가운데
        quarry = lo.Piece(50, "kIdleStoneMine", 2, 2, farm.rel, True, 2 * size, 1.0, 1.0, False)
        stones = [lo.Piece(i, "kBoulder", 1, 1, frozenset({(0, 0)}), True, 0.0, 1.0, 1.0, False) for i in range(60, 72)]
        it, rest = lc._unit(quarry, stones, 0.0, False, size)
        self.assertEqual(((it.w, it.h), len(it.members) - 1, rest), ((4, 4), 12, []))


class ShapeOutlineTest(unittest.TestCase):
    """배치도·재배치 HUD 가 ㄱ·ㅜ 자 건물을 사각형이 아니라 모양 그대로 그리는지."""

    def test_l_shape_outline(self):
        from src.engine.layout import mask_outline
        ell = {(0, 0), (1, 0), (0, 1)}                        # ㄱ 자 3칸 (오른쪽 위가 빔)
        self.assertEqual(sorted(mask_outline(ell)), sorted([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]))
        self.assertEqual(len(mask_outline({(0, 0), (1, 0), (0, 1), (1, 1)})), 4)   # 네모는 꼭짓점 4개

    def test_turn_mask_matches_piece_rotation(self):
        from src.engine.layout import turn_mask
        tee = frozenset({(0, 1), (1, 1), (2, 1), (1, 0)})      # ㅜ 자 (3×2)
        p = lo.Piece(1, "kX", 3, 2, tee, True, 0.0, 1.0, 1.0, False)
        for k in range(4):
            q = lo.rotated(p, k)
            self.assertEqual(turn_mask(set(tee), 3, 2, k), (set(q.rel), q.w, q.h))

    def test_target_outline_rotates_with_building(self):
        from src.engine.layout import Bld, shape_outline_world
        b = Bld(1, "kVilla", 0.0, 0.0, 2, 2, 0, 3.0)
        ell = {(0, 0), (1, 0), (0, 1)}
        now = shape_outline_world(b, ell, 1.0)
        turned = shape_outline_world(b, ell, 1.0, (10.0, 0.0), 1)
        self.assertEqual(len(now), 6)
        self.assertEqual(len(turned), 6)
        self.assertNotEqual(sorted((x - 10, y) for x, y in turned), sorted(now))   # 돌린 모양은 다름

    def test_held_building_keeps_its_shape(self):
        """재배치 모드에서 집어 든 건물은 게임이 충돌 모양을 빼고 보낸다 → 마지막 모양을 돌려 채운다 (사각형으로 안 보이게)."""
        from src.engine.layout import fill_missing_colliders
        tee = [{"id": 7, "shape": "box", "pts": [[0, 1], [3, 1], [3, 2], [0, 2]]},
               {"id": 7, "shape": "box", "pts": [[1, 0], [2, 0], [2, 1], [1, 1]]}]
        b = {"id": 7, "type": "kAlchemist", "x": 1.5, "y": 1.0, "rot": 0, "tw": 3, "th": 2}
        cache = {}
        fill_missing_colliders({"buildings": [b], "geo": {"colliders": tee}}, cache)
        held = {"buildings": [dict(b, x=11.5, rot=1)], "geo": {"colliders": [{"id": 1, "shape": "box", "pts": []}]}}
        got = [c for c in fill_missing_colliders(held, cache)["geo"]["colliders"] if c["id"] == 7]
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["pts"][0], [11.5, 2.5])               # 왼쪽 끝 (0, 1) → 시계 90° 돌리면 위쪽 끝, 중심 (11.5, 1) 로
        self.assertEqual(held["geo"]["colliders"][0]["id"], 1)         # 원본은 그대로

    def test_housing_does_not_need_korean_game_text(self):
        # 윈도우가 한국어가 아니면 게임 문구가 다른 언어로 추출돼 '거처' 낱말이 없다 — 그래도 거처를 알아야 가이드 배치가 된다
        orig = lo._game_text
        try:
            lo._game_text = lambda: {"buildings": {"villa": {"desc_ko": "Home of the Cogitator"}}}
            lo.housing_types.cache_clear()
            lo.house_characters.cache_clear()
            self.assertIn("veteranhut", lo.housing_types())
            self.assertEqual(lo.house_characters()["villa"][0], "cogitator")
        finally:
            lo._game_text = orig
            lo.housing_types.cache_clear()
            lo.house_characters.cache_clear()


if __name__ == "__main__":
    unittest.main()
