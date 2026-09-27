"""로컬 큐의 중복 방지·완료 대기·전달 실패 처리를 실제 전송 없이 확인한다."""
import subprocess
import unittest
from unittest.mock import patch
from tools.local_improvement import due, acknowledge, deliver


class LocalImprovementTest(unittest.TestCase):
    def state(self):
        return dict(enabled=True, pending=None, mode='continue', interval=300,
                    next_at=100, baseline='old', codex='codex.exe', thread='test-thread')

    def test_pending_and_interval_prevent_duplicate_requests(self):
        s=self.state()
        self.assertFalse(due(s,'new',99))
        self.assertTrue(due(s,'new',100))
        s['pending']={'id':'one'}
        self.assertFalse(due(s,'new',10000))

    def test_wait_requires_new_evidence_but_continue_does_not(self):
        s=self.state();s['mode']='wait'
        self.assertFalse(due(s,'old',101))
        self.assertTrue(due(s,'new',101))
        s['enabled']=False
        self.assertFalse(due(s,'new',101))

    def test_ack_must_match_and_respects_stop(self):
        s=self.state();s['pending']={'id':'one'}
        with self.assertRaises(ValueError):acknowledge(s,'other','wait',200)
        acknowledge(s,'one','stop',200)
        self.assertFalse(s['enabled'])
        self.assertIsNone(s['pending'])
        self.assertEqual(s['next_at'],500)

    @patch('tools.local_improvement.save_state')
    def test_success_waits_for_ack_and_does_not_change_model_or_permissions(self,save):
        s=self.state()
        def send(argv,**kwargs):
            self.assertEqual(argv[:5],['codex.exe','queue','--thread','test-thread','--message'])
            self.assertEqual(len(argv),6)
            self.assertEqual(s['pending']['status'],'sending')
            return subprocess.CompletedProcess(argv,0,'queued','')
        deliver(s,'new',send,200)
        self.assertEqual(s['pending']['status'],'queued')
        self.assertFalse(due(s,'newer',1000))

    @patch('tools.local_improvement.save_state')
    def test_ambiguous_timeout_stops_without_resending(self,save):
        s=self.state()
        def send(*args,**kwargs):raise subprocess.TimeoutExpired('codex',30)
        deliver(s,'new',send,200)
        self.assertFalse(s['enabled'])
        self.assertEqual(s['pending']['status'],'unconfirmed')
        self.assertFalse(due(s,'newer',1000))
