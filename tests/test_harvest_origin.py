"""일반 기지의 임시 발사대 좌표를 실제 채집 기록과 혼동하지 않는다."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.tracking.harvest_origin import HarvestOrigin
from src.engine.sim_signature import physical_base


def geometry():
    return dict(left=-.562,right=44.438,bottom=-.562,top=19.688,space_w=1.125,player_y=-.844,
                launcher=[21.95,-.844],colliders=[])


class HarvestOriginTest(unittest.TestCase):
    def test_hidden_placeholder_uses_compatible_real_trace_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'harvest_traces.jsonl').write_text(json.dumps({'geo':geometry()})+'\n',encoding='utf-8')
            origin=HarvestOrigin(folder)
            base={'geo':dict(geometry(),launcher=[0,-3.5]),'buildings':[]}
            result=origin.resolve(base)
            self.assertEqual(base['geo']['launcher'],[0,-3.5])
            self.assertEqual(result['geo']['launcher'],[21.95,-.844])
            self.assertEqual(result['geo']['launcher_source'],'last_observed')
            self.assertEqual(physical_base(result),physical_base({'geo':geometry(),'buildings':[]}))

    def test_absent_or_incompatible_observation_never_invents_a_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            origin=HarvestOrigin(folder)
            hidden={'geo':dict(geometry(),launcher=[0,-3.5])}
            self.assertIsNone(origin.resolve(hidden)['geo']['launcher'])
            origin.resolve({'geo':geometry()})
            hidden['geo']['right']+=9
            self.assertIsNone(origin.resolve(hidden)['geo']['launcher'])

    def test_current_valid_origin_replaces_saved_point_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            origin=HarvestOrigin(folder)
            current={'geo':geometry()}
            self.assertIs(origin.resolve(current),current)
            newer={'geo':dict(geometry(),launcher=[23.1,-.844])}
            origin.resolve(newer)
            restarted=HarvestOrigin(folder)
            result=restarted.resolve({'geo':dict(geometry(),launcher=[0,-3.5])})
            self.assertEqual(result['geo']['launcher'],[23.1,-.844])

    def test_legacy_geometry_without_player_line_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            base={'geo':{'launcher':[1,2]}}
            self.assertIs(HarvestOrigin(folder).resolve(base),base)
