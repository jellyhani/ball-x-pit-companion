"""게임 로그 해석(실제 Player.log 에서 발췌한 입력)과 단축키 반복 입력 처리."""
import os
import unittest

from src.services.input_watch import KeyDebouncer
from src.services.log_watcher import PlayerLogParser, summarize_phase
from tests.helpers import FIXTURES


class LogParserTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXTURES, "player_log_excerpt.txt"), encoding="utf-8") as f:
            self.text = f.read()

    def kinds(self, events):
        return [e.kind for e in events if e.kind != "base_state"]

    def test_events_from_real_log_format(self):
        ev = PlayerLogParser().feed(self.text)
        kinds = self.kinds(ev)
        self.assertEqual(kinds[0], "version")
        self.assertIn("run_started", kinds)
        self.assertEqual(kinds.count("reroll"), 3)
        self.assertIn("level_complete", kinds)
        self.assertIn("run_ended", kinds)
        self.assertEqual([e.value for e in ev if e.kind == "reroll"], ["kVampire", "kThorns", "kPlatinumDumbbell"])
        self.assertEqual(ev[0].value, "Version 1.301 (May 08 2026 #3)")

    def test_split_chunks_give_same_events(self):
        whole = PlayerLogParser().feed(self.text)
        p = PlayerLogParser()
        parts = []
        for i in range(0, len(self.text), 97):
            parts += p.feed(self.text[i:i + 97])
        self.assertEqual(whole, parts)

    def test_phase_summary(self):
        self.assertEqual(summarize_phase(PlayerLogParser().feed(self.text)), "base")
        start_only = self.text.split("kNormal -> kReturningFromLvl")[0]
        # 마지막 블록이 잘린 경우도 앞부분만으로 판단한다
        self.assertEqual(summarize_phase(PlayerLogParser().feed(start_only)), "in_run")

    def test_garbage_does_not_raise(self):
        self.assertEqual(PlayerLogParser().feed("\x00\xff 이상한 줄\n\n\n{\"a\": 1}\n\n"), [])


class DebounceTest(unittest.TestCase):
    def test_held_key_toggles_once(self):
        hits = []
        d = KeyDebouncer({"f9": lambda: hits.append(1)})
        for _ in range(10):
            d.press("f9")          # 키를 누르고 있으면 반복 입력이 들어온다
        d.release("f9")
        d.press("f9")
        self.assertEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
