import json
import os
import unittest

from tests.helpers import game_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GameDataTest(unittest.TestCase):
    def test_official_korean_names_from_game_files(self):
        d = game_data()
        # 기존 사전의 추측 번역('과다출혈', '철', '빙결')이 아니라 게임 공식 번역
        self.assertEqual(d.name("ball:hemorrhage"), "대출혈")
        self.assertEqual(d.name("ball:heavy"), "무쇠")
        self.assertEqual(d.name("ball:freeze"), "냉동")
        self.assertEqual(d.name("passive:thorns"), "가시 왕관")
        self.assertEqual(d.name("char:itchyfinger"), "난사광")

    def test_counts_and_sources(self):
        d = game_data()
        self.assertEqual(sum(1 for i in d.items.values() if i.kind == "ball"), 90)
        self.assertEqual(sum(1 for i in d.items.values() if i.kind == "passive"), 71)
        self.assertEqual(len(d.characters), 23)
        self.assertEqual(d.game_build_id, "23150541")

    def test_official_description_keeps_max_health_wording(self):
        # 위키는 '현재 체력', 게임 원문은 '최대 체력'. 원문을 따른다.
        self.assertIn("최대 체력", game_data().items["ball:hemorrhage"].desc_ko)

    def test_log_ids_map_to_items(self):
        d = game_data()
        for log_id, expected in (("kVampire", "ball:vampire"), ("kThorns", "passive:thorns"),
                                 ("kReachersSpear", "passive:reachersspear"), ("kIronOnesie", "passive:irononesie"),
                                 ("kPlatinumDumbbell", "passive:platinumdumbbell"), ("kBurn", "ball:burn")):
            self.assertEqual(d.item_by_log_id(log_id), expected, log_id)

    def test_every_item_and_character_has_extracted_image(self):
        d = game_data()
        from src.gamedata import DATA_DIR
        if not os.path.exists(os.path.join(DATA_DIR, "icon_manifest.json")):
            self.skipTest("아이콘 미추출 — tools/setup_data.py")
        with open(os.path.join(DATA_DIR, "icon_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["missing_icons"], [])
        self.assertEqual(manifest["missing_portraits"], [])
        for item_id in d.items:
            self.assertTrue(os.path.exists(os.path.join(DATA_DIR, "icons", item_id.replace(":", "_") + ".png")))

    def test_recipes_resolve_to_known_items(self):
        d = game_data()
        self.assertTrue(d.recipes)
        hem = d.recipes_for("ball:hemorrhage")
        # 게임 안 레시피 (위키에는 출혈+무쇠만 있었으나 게임에는 출혈+살점도 있다)
        self.assertIn({"ball:bleed", "ball:heavy"}, [set(r.ingredients) for r in hem])
        for r in d.recipes:
            self.assertIn(r.result, d.items)
            for i in r.ingredients:
                self.assertIn(i, d.items)

    def test_rule_tags_reference_real_items(self):
        d = game_data()
        for tag, spec in d.rules["tags"].items():
            for i in spec["items"]:
                self.assertIn(i, d.items, f"{tag}: {i}")
        for cid in d.rules["characters"]:
            self.assertIn(cid, d.characters)
        # 거머리는 공식 설명에 회복이 없다 → 회복 태그에 없어야 한다
        self.assertFalse(d.has_tag("ball:leech", "heal_source"))


if __name__ == "__main__":
    unittest.main()
