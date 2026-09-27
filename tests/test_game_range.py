"""게임의 대상 사각형·회전 셀 겹침을 중심 근사와 구분해 검사한다."""
import unittest
from src.engine import game_range as gr, layout_opt as lo, native_layout as nl, native
from src.engine.layout import Grid
from src.engine.layout_guide import covered_members


class GameRangeTest(unittest.TestCase):
    def test_observed_truth_does_not_follow_a_moved_candidate(self):
        source=dict(id=1,x=0,y=0,rot=0,range=1,observed_pose=[0,0,0],in_range_ids=[2])
        target=dict(id=2,x=2,y=0,rot=0,observed_pose=[2,0,0],range_boxes=[[0,0,0,0]])
        self.assertTrue(gr.row_in_range(source,target))
        from src.tracking.game_contract import validate_ranges,range_signature
        base={'buildings':[source,target]}
        report=validate_ranges(base)
        self.assertEqual(report['mismatches'],[[1,2,True,False]])
        before=range_signature(base)
        target['x']=3
        self.assertNotEqual(before,range_signature(base))
        self.assertFalse(gr.row_in_range(source,target))

    def test_range_verifier_reports_missing_target_geometry(self):
        from src.tracking.game_contract import validate_ranges
        self.assertEqual(validate_ranges({'buildings':[
            dict(id=1,x=0,y=0,range=2,in_range_ids=[2]),dict(id=2,x=0,y=0)]})['missing'],[2])

    def test_box_overlap_can_include_center_outside_range(self):
        source={'x':0.,'y':0.,'range':1.}
        box={'x':2.,'y':0.,'range_boxes':[[-1.,-.5,1.,.5]]}
        self.assertTrue(gr.row_in_range(source,box))
        box['range_boxes']=[[0,0,0,0]]  # 원형 건물은 게임의 중심 분기를 따른다.
        self.assertFalse(gr.row_in_range(source,box,pad=10.))

    def test_sparse_shape_rotation_preserves_holes(self):
        row={'rot':1,'range_rotation':0,'range_boxes':[[1,0,2,.25]]}
        self.assertEqual(gr.boxes_from_row(row),((0,-2,.25,-1),))
        self.assertFalse(gr.overlaps(0,0,.5,gr.boxes_from_row(row)))

    def test_piece_rotation_and_native_score_use_exact_target_extent(self):
        source=lo.Piece(1,'kIdleFarm',2,2,frozenset((x,y) for x in range(2) for y in range(2)),True,1.)
        target=lo.Piece(2,'kDenseWheat',1,1,frozenset({(0,0)}),True,0.,range_boxes=((-0.5,-0.5,.5,.5),))
        pieces={1:source,2:target}
        grid=Grid(0.,0.,1.,{(x,y) for x in range(8) for y in range(8)})
        origins={1:(0,0),2:(2,0)}
        lay=lo.Layout(grid,pieces,origins)
        scorer=lo.Scorer(pieces,set(),set(),pad=5.)
        # 정확한 target에는 거짓 +5 여유를 더하지 않는다.
        self.assertEqual(scorer.score(lay)[1]['kIdleFarm'],1)
        self.assertEqual(covered_members(grid,pieces,origins,[(source,[2])],5.),{1:{2}})
        if native.lib():
            self.assertAlmostEqual(nl.score(lay,scorer),scorer.score(lay)[0])
        lay.origin[2]=(6,0)
        self.assertEqual(scorer.score(lay)[1]['kIdleFarm'],0)
        if native.lib():
            self.assertAlmostEqual(nl.score(lay,scorer),scorer.score(lay)[0])
        asymmetric=lo.Piece(3,'kHome',2,1,frozenset({(0,0),(1,0)}),True,0.,range_boxes=((0,0,1,.25),))
        self.assertEqual(lo.rotated(asymmetric,1).range_boxes,((0,-1,.25,0),))
