"""현재 선택지의 실효 수치를 사용하며 원본/다른 항목의 레벨 표는 변경하지 않는다."""


def is_current(card, before, after):
    value = card.effective
    if not value or value.get("scope") != "current_run_uncombined" or value.get("level") != after:
        return False
    if before is not None and not isinstance(value.get("before"), dict):
        return False
    return isinstance(value.get("after"), dict)


def level_rows(raw, card, before, after):
    """표시 레벨(1부터 시작)에 맞춰 실효 수치만 사본에 넣는다.

    raw의 각 원소 인덱스는 레벨보다 1 작다. 신뢰할 수 있는 현재 런의 앞뒤 값이
    함께 있을 때만 대체하여, 기본 수치와 강화가 적용된 수치를 섞은 증가율을 피한다.
    """
    if not is_current(card, before, after):
        return raw  # 원본 이전값과 실효 이후값을 섞어 증가율을 만들지 않는다.
    value = card.effective
    rows = [dict(row) for row in (raw or [])]
    while len(rows) < after:
        rows.append({})
    rows[after - 1] = dict(value["after"])
    if before is not None and before > 0:
        rows[before - 1] = dict(value["before"])
    return rows
