"""읽기 전용 관측의 누락·반복·부분 행 처리를 검증한다."""
import json
from pathlib import Path
import tempfile
import unittest
from tools.improvement_audit import inspect, tail


class ImprovementAuditTest(unittest.TestCase):
    def test_missing_live_data_is_unknown_and_second_observation_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            first = inspect(p, now=1)
            self.assertFalse(first['live']['readable'])
            self.assertIsNone(first['live']['age_seconds'])
            self.assertEqual(inspect(p, first, now=2)['changed'], [])
            self.assertEqual(list(p.iterdir()), [])

    def test_heartbeat_and_stock_do_not_retrigger_but_new_harvest_does(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            state = {'seq': 1, 'base': {'buildings': [{'id': 1, 'type': 'kGrandTree', 'x': 2, 'res': 7}]}}
            live = p / 'live_state.json'
            live.write_text(json.dumps(state))
            first = inspect(p)
            state['seq'] = 2
            state['base']['buildings'][0]['res'] = 0
            live.write_text(json.dumps(state))
            self.assertEqual(inspect(p, first)['changed'], [])
            (p / 'harvest.jsonl').write_text('{"t": 2, "gain": [0,1,2,3]}\n')
            self.assertEqual(inspect(p, first)['changed'], ['harvest.jsonl'])

    def test_tail_keeps_complete_first_line_and_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'harvest.jsonl'
            p.write_bytes(b'111\n222\n333\n')
            self.assertEqual(tail(p, 8), [b'222', b'333'])
            self.assertEqual(tail(p, 7), [b'333'])
            p.write_bytes(b'{"t":1}\n{unfinished')
            result = inspect(Path(folder))
            self.assertEqual(result['events']['harvest.jsonl']['invalid_rows'], 1)
            self.assertTrue(result['warnings'])
