"""개발용 재생 도구: 게임 스크린샷 한 장을 실제 인식·추천 경로에 넣고, 실제 Qt HUD를 그려 결과 이미지를 만든다.

운영 연동이 아니다. 게임 없이 화면 인식·추천·HUD 배치·글자 크기를 눈으로 확인하는 용도다.

    .venv\\Scripts\\python.exe tools\\replay_screenshot.py 스크린샷.png [출력폴더]
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv) -> int:
    from PIL import Image
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from src.domain import FrameInfo, ScreenKind
    from src.engine.recommender import Recommender
    from src.gamedata import load_game_data
    from src.recognition.level_up_reader import LevelUpReader
    from src.recognition.ocr import OcrReader
    from src.recognition.screen_parser import parse_layout
    from src.tracking.choice_tracker import ChoiceTracker
    from src.tracking.run_state import RunState
    from src.ui import geometry as geo
    from src.ui.control_window import ControlWindow
    from src.services.settings import Settings
    from src.ui.hud import RecommendationHud
    from src.ui.card_highlight import CardHighlight

    src_path = argv[1]
    out_dir = argv[2] if len(argv) > 2 else os.path.join(ROOT, "scratch", "replay")
    os.makedirs(out_dir, exist_ok=True)
    img = Image.open(src_path).convert("RGB")
    frame = FrameInfo(1, 0.0, (0, 0), img.size, "재생")

    data = load_game_data()
    parsed = parse_layout(OcrReader().read(img), frame)
    print("화면:", parsed.kind.value, parsed.note)
    if parsed.kind != ScreenKind.LEVEL_UP:
        return 1
    from src.recognition.text_match import NameIndex

    names = NameIndex((index.id, index.name_ko) for index in data.items.values())
    observation = LevelUpReader(OcrReader(target_height=100000).read, names=names).read(
        img, frame, parsed.layout
    )

    run = RunState()
    run.start_run()
    tracker = ChoiceTracker()
    event = tracker.observe(observation, 0.0, forced=True)[0]
    s = event.session
    run.apply_character(s.character_id)
    if s.inventory is not None:
        run.apply_inventory(s.inventory, data)
    record = Recommender(data).recommend(s, run)
    print("설명 패널:", data.name(observation.hover_item_id) if observation.hover_item_id else "-")
    print("캐릭터:", data.name(s.character_id), "골드:", s.gold, "새로고침 비용:", s.reroll_cost)
    print("보유:", [(data.name(o.item_id), o.level) for o in run.owned.values()])
    print(
        "카드:",
        [
            (card.position, data.name(card.item_id), card.label and card.label.value, card.shown_level)
            for card in s.cards
        ],
    )
    print("추천:", record.status, record.headline, "|", record.reroll_text)
    for evaluation in record.evals:
        print(
            "  ",
            evaluation.card.position,
            data.name(evaluation.card.item_id),
            evaluation.action_text,
            f"{evaluation.score:.0f}",
            [reason.text for reason in evaluation.reasons],
            [warning.text for warning in evaluation.warnings],
        )

    for scale, name in ((1.0, "normal"), (1.2, "large")):
        hud = RecommendationHud(data, scale)
        hud.show_recommendation(record, s.points_left)
        hud.adjustSize()
        hud.show()
        app.processEvents()
        pix = hud.grab()
        hud_path = os.path.join(out_dir, f"hud_{name}.png")
        pix.save(hud_path)
        # 게임 화면 위에 실제 배치 위치로 합성 (재생 이미지는 배율 1로 본다)
        rectangle = QRect(0, 0, img.width, img.height)
        cards = [QRect(*card.rect) for card in s.cards]
        p = geo.place_hud(
            rectangle, cards, pix.width(), pix.height(), panel=QRect(*s.panel_rect) if s.panel_rect else None
        )
        hud_img = Image.open(hud_path).convert("RGBA")
        comp = img.convert("RGBA")
        if record.status not in ("auto", "none"):
            from src.engine.recommender import card_verdict

            hl = CardHighlight()
            hl.set_marks(
                [
                    (QRect(*evaluation.card.rect), card_verdict(record, evaluation))
                    for evaluation in record.evals
                ]
            )
            hl.show()
            app.processEvents()
            hl_path = os.path.join(out_dir, "highlight.png")
            hl.grab().save(hl_path)
            comp.alpha_composite(Image.open(hl_path).convert("RGBA"), (hl.x(), hl.y()))
            hl.close()
        comp.alpha_composite(hud_img, (max(0, p.x()), max(0, p.y())))
        comp.convert("RGB").save(os.path.join(out_dir, f"overlay_{name}.png"))
        hud.close()
        print(f"HUD {name}: {pix.width()}×{pix.height()} 위치 {p.x()},{p.y()}")

    win = ControlWindow(data, run, Settings())
    win.resize(560, 760)
    win.show()
    app.processEvents()
    win.grab().save(os.path.join(out_dir, "control_run.png"))
    win.select_page("도감")
    win.search.setText("출혈")
    win.pedia_list.setCurrentRow(0)
    app.processEvents()
    win.grab().save(os.path.join(out_dir, "control_pedia.png"))
    win.select_page("설정")
    app.processEvents()
    win.grab().save(os.path.join(out_dir, "control_settings.png"))
    print("저장:", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
