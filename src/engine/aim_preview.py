"""계산된 첫 작업자 경로의 표시 범위. 물리 계산이나 추천 점수는 바꾸지 않는다."""
from __future__ import annotations

# 표시 취향의 상한이다. 정확도나 게임의 반사 횟수 제한을 뜻하지 않는다.
MAX_REFLECTIONS = 7
LENGTHS = {"short": 1, "normal": 3, "long": MAX_REFLECTIONS}


def worker_preview(workers):
    """시뮬레이터는 출발점·고체 반사점·종료점만 기록한다. 통과 채집은 점을 추가하지 않는다.

    반사 뒤 나가는 방향까지 보이도록 다음 점 하나를 포함한다. 계산 종료점 밖으로 연장하지 않는다.
    """
    return [(x, y) for x, y, _ in workers[0].path[:MAX_REFLECTIONS + 2]] if workers else []


def visible_path(path, length="normal", *, recommended=False, extended=False):
    """긴 경로를 재계산하지 않고 잘라 표시한다. 보조키는 두 경로 모두 최대 길이로 펼친다."""
    count = MAX_REFLECTIONS if extended else (1 if recommended else LENGTHS.get(length, 3))
    return list(path or [])[:count + 2]
