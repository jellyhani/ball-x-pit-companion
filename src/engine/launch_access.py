"""게임의 발사 금지 조건을 이용한 배치 후보 검사와 입구 복구.

1.301 BallPreview.SetAimDirWorker(0x4B6120): 첫 Raycast 접점 y < -0.5,
normal.y < 0, abs(normal.x) <= 0.1이면 금지. GetRCHit(0x4B5C10)은
채집 관통 강화와 무관하게 첫 충돌을 읽는다. 실제 화면의 허용 여부는 브리지 값이 우선이다.
"""
import math
from . import harvest_sim as hs
from . import layout_opt as lo
from .layout import buildings_from_base, grid_from_geo, shape_masks, building_cells, occupied, free_spots, moved_base, Move
from .layout_guide import preserves_guide
from ..i18n import tr


def launchable(world, angle):
    x,y=world.launcher
    dx,dy=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    hits=[hit for s in world.shapes if (hit:=hs._hit_shape(s,x,y,dx,dy,0.)) is not None]
    if not hits:
        return True
    distance,nx,ny=min(hits,key=lambda h:h[0])
    length=math.hypot(nx,ny)
    return not (y+dy*distance < -.5 and ny < 0 and length > 0 and abs(nx)/length <= .1000000015)


def allowed_angles(geo, angles):
    world=hs.world_from_geo(geo,0.)
    return list(angles) if not isinstance(world,hs.World) else [a for a in angles if launchable(world,a)]


def modeled_current_launch(base):
    """구형 브리지용 대체 판정. 최신 조준 위치·방향·충돌 자료가 충분할 때만 반환한다."""
    if base.get('state') != 'kAimWorkers':
        return None
    geo=base.get('geo') or {};player=base.get('player') or []
    point=geo.get('launcher')
    try:
        if (not isinstance(point,(list,tuple)) or len(point)!=2 or len(player)<4
                or not geo.get('colliders') or 'player_y' not in geo):
            return None
        values=[*point,player[2],player[3],geo['player_y'],geo['left'],geo['right']]
        if not all(type(v) in (int,float) and math.isfinite(v) for v in values):
            return None
        if (not geo['left'] <= point[0] <= geo['right'] or abs(point[1]-geo['player_y'])>.002
                or player[3]<=0 or math.hypot(player[2],player[3])<1e-6):
            return None
        world=hs.world_from_geo(geo,0.)
        if world is None:
            return None
        return launchable(world,math.degrees(math.atan2(player[3],player[2])))
    except (TypeError,ValueError,KeyError):
        return None


def entrance_blockers(base):
    grid=grid_from_geo(base.get('geo') or {})
    if grid is None:
        return set()
    buildings=buildings_from_base(base)
    masks=shape_masks(base.get('geo') or {},buildings,grid)
    cells=lo.entrance_cells(base.get('geo') or {},grid)
    return {i for i,b in buildings.items() if building_cells(b,grid,masks.get(i)) & cells}


def repair_entrance(base, pad=0., accept=None):
    """입구 정리는 점수 개선 문턱과 별개다. 가까운 빈자리 중 기존 범위 효과를 가장 많이 보존한다."""
    grid=grid_from_geo(base.get('geo') or {})
    if grid is None:
        return [],base
    entrance=lo.entrance_cells(base.get('geo') or {},grid)
    current,moves=base,[]
    for bid in sorted(entrance_blockers(base)):
        buildings=buildings_from_base(current);b=buildings[bid]
        raw=next(r for r in current['buildings'] if r['id']==bid)
        if b.type in lo.FIXED_TYPES or raw.get('state') in hs.CONSTRUCTION_STATES:
            continue
        masks=shape_masks(current.get('geo') or {},buildings,grid)
        occ=occupied(buildings,grid,skip=[bid],masks=masks)
        spots=free_spots(grid,occ|entrance,*b.footprint)
        spots.sort(key=lambda p:(p[0]-b.x)**2+(p[1]-b.y)**2)
        candidates=[]
        for pos in spots[:32]:
            candidate=moved_base(current,buildings,bid,pos)
            if not lo.preserves_production(base,candidate,pad) or not preserves_guide(base,candidate,pad):
                continue
            pcs,origin=lo.pieces_from_base(candidate,grid,lo.housing_types())
            scorer=lo.Scorer(pcs,lo._stat_types(candidate),lo.housing_types(),pad=pad)
            score=scorer.score(lo.Layout(grid,pcs,origin))[0]
            candidates.append((score,pos,candidate))
        candidates.sort(key=lambda row:-row[0])
        for _,pos,candidate in candidates:
            if accept is not None and not accept(candidate):
                continue
            moves.append(Move(bid,pos,0.,tr("발사 입구를 비우기 위해 이동")))
            current=candidate
            break
    return moves,current
