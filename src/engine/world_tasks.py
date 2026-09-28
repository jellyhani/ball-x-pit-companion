"""WorldTimeMgr와 BuildingInst.AddTaskSecs의 자원 재생·자동 수확 분기.

게임 1.301의 4C9B30 / 460E40 / 4653C0을 확인했다. 화면 프레임 순서는 근사다.
입력은 시뮬레이션 소유 사본이며 실제 게임 객체를 바꾸지 않는다.
"""

import math
from .game_range import row_in_range


class TaskClock:
    """한 번의 발사 예측에서 자원 재생과 자동 수확의 재고를 공유한다.

    clock은 (세계 시계의 소수 진행량, 갱신 주기, 게임 배속)이다. next는 Worker.t와
    같은 게임 시간 단위이고, elapsed는 건물별 누적 세계 초다. buildings/stocks/notes는
    시뮬레이션이 소유한 사본을 참조한다. gain은 작업자가 아닌 자동 생산으로 얻은 자원이다.
    """

    def __init__(self, clock, buildings, stocks, resource_kind, resource_tile, notes):
        self.buildings = buildings
        self.stocks = stocks
        self.kind = resource_kind
        self.is_resource = resource_tile
        self.notes = notes
        self.gain = [0, 0, 0, 0]
        self.elapsed = {
            building_id: building.get("task_seconds", 0) for building_id, building in buildings.items()
        }
        self.rate = 1.0
        self.next = math.inf
        if not any(
            self.is_resource(building) or building.get("is_idle_harvester") for building in buildings.values()
        ):
            return
        if not clock:
            notes.add("missing_world_task_clock")
            return
        phase, interval, speed = clock
        if interval != 1 or not math.isfinite(speed) or speed <= 0 or not 0 <= phase < 1:
            notes.add("missing_world_task_clock")
            return
        # 게임은 시간 정지·감속 중에도 실제 시간만큼 세계 작업을 진행한다.
        # 정상 배속 이상에서는 두 시계가 함께 가고, 감속에서는 세계 시계가 상대적으로 빠르다.
        self.rate = max(speed, 1.0) / speed
        self.next = (1.0 - phase) / self.rate
        notes.add("world_task_frame_order_approximation")

    def tick(self):
        """보관 목록의 순서대로 처리한다. 가득 찬 자원의 작업 시계는 멈춘다."""
        changed = False
        for building_id, building in self.buildings.items():
            if building.get("state") == "kScaffold":
                continue
            resource = self.is_resource(building)
            idle = building.get("is_idle_harvester") is True
            if not resource and not idle:
                if building.get("task_active"):
                    self.notes.add("unsupported_world_task:" + str(building.get("type", "")))
                continue
            period = building.get("task_target_seconds")
            if type(period) not in (int, float) or not math.isfinite(period) or period <= 0:
                self.notes.add("missing_world_task_period")
                continue
            if resource:
                if self.stocks.get(building_id, 0) >= building.get("cap", 0):
                    continue
            elif building.get("task_active") is not True:
                continue
            self.elapsed[building_id] += 1
            if self.elapsed[building_id] < period:
                continue
            self.elapsed[building_id] = 0
            if resource:
                self.stocks[building_id] = self.stocks.get(building_id, 0) + 1
                self.sync(building_id)
                changed = True
            else:
                kind = building.get("production_resource")
                if type(kind) is not int or kind not in (1, 2, 3):
                    self.notes.add("missing_production_resource")
                    continue
                for target_id, target in self.buildings.items():
                    if target_id == building_id or not self.is_resource(target) or self.kind(target) != kind:
                        continue
                    stock = self.stocks.get(target_id, 0)
                    if stock <= 0 or not row_in_range(building, target):
                        continue
                    amount = min(stock, max(0, int(building.get("lvl", 0))) + 1)
                    self.stocks[target_id] -= amount
                    self.gain[kind] += amount
                    self.sync(target_id)
                    changed = True
        self.next += 1.0 / self.rate
        return changed

    def sync(self, building_id):
        """재고가 비거나 다시 생긴 뒤의 충돌 계층을 같은 사본에 반영한다."""
        building = self.buildings[building_id]
        stock_count = self.stocks[building_id]
        kind = self.kind(building)
        building.update(
            res=stock_count,
            can_harvest=stock_count > 0,
            raycast_enabled=stock_count > 0 and kind != 1,
            pickup_enabled=stock_count > 0 and kind == 1,
        )
