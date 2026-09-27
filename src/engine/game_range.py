"""BuildingUtl.IsInRange의 대상 영역 겹침. 중심 근사와 구분한다."""
import math


def rotate_boxes(boxes, turns):
    if boxes is None:
        return None
    out=[]
    for x0,y0,x1,y1 in boxes:
        points=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
        for _ in range(turns%4):
            points=[(y,-x) for x,y in points]
        out.append((min(x for x,y in points),min(y for x,y in points),
                    max(x for x,y in points),max(y for x,y in points)))
    return tuple(out)


def boxes_from_row(row):
    boxes=row.get('range_boxes')
    if not isinstance(boxes,(list,tuple)):
        return None
    if any(not isinstance(b,(list,tuple)) or len(b)!=4 or
           not all(type(v) in (int,float) and math.isfinite(v) for v in b)
           or b[0]>b[2] or b[1]>b[3] for b in boxes):
        return None
    return rotate_boxes(boxes,int(row.get('rot',0))-int(row.get('range_rotation',row.get('rot',0))))


def overlaps(dx,dy,radius,boxes):
    """대상 영역은 상대 좌표다. 경계 접촉도 게임처럼 범위 안으로 인정한다."""
    return any(dx+x0<=radius and dx+x1>=-radius and dy+y0<=radius and dy+y1>=-radius
               for x0,y0,x1,y1 in boxes)


def row_in_range(source,target,pad=0.):
    def unchanged(row):
        return row.get('observed_pose')==[row.get('x'),row.get('y'),row.get('rot',0)]
    if isinstance(source.get('in_range_ids'),list) and unchanged(source) and unchanged(target):
        return target.get('id') in source['in_range_ids']
    boxes=boxes_from_row(target)
    dx,dy=target['x']-source['x'],target['y']-source['y']
    if boxes is not None:
        return overlaps(dx,dy,float(source.get('range',0)),boxes)
    # 옛 브리지의 근사 경로는 정확한 겹침 자료가 없을 때만 유지한다.
    from .layout_opt import in_range
    return in_range(dx,dy,float(source.get('range',0))+pad)
