"""게임의 대상별 실제 포함 판정과 도우미 수식을 독립적으로 비교한다."""
from ..engine.game_range import boxes_from_row,overlaps


def range_signature(base):
    from ..engine.sim_signature import digest
    fields=('id','x','y','rot','range','range_boxes','range_rotation','observed_pose','in_range_ids')
    return digest([{k:b[k] for k in fields if k in b} for b in base.get('buildings',[])])


def validate_ranges(base):
    rows={b['id']:b for b in base.get('buildings',[]) if 'id' in b}
    boxes={i:boxes_from_row(b) for i,b in rows.items()}
    report={'checked':0,'mismatches':[],'missing':[]}
    for sid,source in rows.items():
        ids=source.get('in_range_ids')
        if not isinstance(ids,list):continue
        expected=set(ids)
        for tid,target in rows.items():
            if tid==sid:continue
            if boxes[tid] is None:
                if tid not in report['missing']:report['missing'].append(tid)
                continue
            if not all(k in source and k in target for k in ('x','y')) or 'range' not in source:
                continue
            # row_in_range의 관측값 우선 경로를 부르지 않는다. 같은 값을 자기 자신과 비교하면 안 된다.
            actual=overlaps(target['x']-source['x'],target['y']-source['y'],source['range'],boxes[tid])
            report['checked']+=1
            if actual!=(tid in expected):
                report['mismatches'].append([sid,tid,tid in expected,actual])
    return report
