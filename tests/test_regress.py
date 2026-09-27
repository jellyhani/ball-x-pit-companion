"""추천 결과 누락을 성공으로 처리하지 않도록 검증 도구 자체를 확인한다."""
import unittest
from tools.regress import compare


class RegressionComparisonTest(unittest.TestCase):
    def test_missing_all_or_some_results_fails(self):
        expected = [{"seq": 1, "best": "a"}, {"seq": 2, "best": "b"}]
        self.assertEqual(len(compare(expected, [])), 2)
        self.assertIn("실제 결과 누락", compare(expected, expected[:1])[0])

    def test_duplicate_or_absent_identity_fails_even_with_matching_contents(self):
        row = {"seq": 1, "best": "a"}
        self.assertTrue(compare([row], [row, row]))
        self.assertTrue(compare([row, row], [row]))
        self.assertTrue(compare([{"best": "a"}], [{"best": "a"}]))

    def test_unchanged_or_reordered_results_pass(self):
        rows = [{"seq": 1, "best": "a"}, {"seq": 2, "best": "b"}]
        self.assertEqual(compare(rows, rows[::-1]), [])

    def test_new_or_changed_result_fails(self):
        self.assertTrue(compare([], [{"seq": 1}]))
        self.assertTrue(compare([{"seq": 1, "best": "a"}], [{"seq": 1, "best": "b"}]))
