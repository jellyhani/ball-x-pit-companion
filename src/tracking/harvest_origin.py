"""일반 기지의 숨겨진 발사대 위치 대신 같은 기지에서 실제 관측한 채집 위치를 사용한다."""

import json
import math
from pathlib import Path


FIELDS = ("left", "right", "bottom", "top", "space_w", "player_y")


def valid_origin(geo):
    point = geo.get("launcher")
    values = [geo.get(k) for k in FIELDS]
    if (
        not isinstance(point, (list, tuple))
        or len(point) < 2
        or not all(type(value) in (int, float) and math.isfinite(value) for value in [*values, *point[:2]])
    ):
        return False
    # 플러그인이 소수 셋째 자리까지 보내므로 반올림 오차만 허용한다.
    return geo["left"] <= point[0] <= geo["right"] and abs(point[1] - geo["player_y"]) <= 0.002


class HarvestOrigin:
    def __init__(self, folder):
        self.path = Path(folder) / "harvest_origin.json"
        self.saved = None
        try:
            geo = json.loads(self.path.read_text(encoding="utf-8"))
            if valid_origin(geo):
                self.saved = geo
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if self.saved is None:
            # 이전 버전이 남긴 실제 채집 기록도 재사용한다. 큰 기록 파일 전체를 메모리에 올리지 않는다.
            try:
                with (Path(folder) / "harvest_traces.jsonl").open("rb") as file_handle:
                    file_handle.seek(0, 2)
                    file_handle.seek(max(0, file_handle.tell() - 4_000_000))
                    lines = file_handle.read().splitlines()
                for line in reversed(lines):
                    try:
                        geo = json.loads(line).get("geo") or {}
                        if valid_origin(geo):
                            self.saved = self._record(geo)
                            break
                    except (ValueError, TypeError, AttributeError):
                        continue
            except OSError:
                pass

    @staticmethod
    def _record(geo):
        return {**{k: geo[k] for k in FIELDS}, "launcher": list(geo["launcher"][:2])}

    def resolve(self, base):
        geo = base.get("geo") or {}
        if "player_y" not in geo:
            return base  # 채집선 높이를 보내지 않던 구형 입력과 호환.
        if valid_origin(geo):
            saved = self._record(geo)
            if saved != self.saved:
                self.saved = saved
                try:
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(saved), encoding="utf-8")
                    tmp.replace(self.path)
                except OSError:
                    pass
            return base
        known = self.saved
        compatible = known is not None and all(
            type(geo.get(k)) in (int, float) and abs(geo[k] - known[k]) <= 0.002 for k in FIELDS
        )
        corrected = dict(
            geo,
            launcher=list(known["launcher"]) if compatible else None,
            launcher_source="last_observed" if compatible else "unavailable",
        )
        return dict(base, geo=corrected)
