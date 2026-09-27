"""설치된 의존성의 라이선스 원문을 배포물에 넣고 버전별 목록을 만든다."""
import json
from importlib import metadata
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def collect_notices():
    from packaging.requirements import Requirement
    pending=[]
    for file in ['requirements.txt','requirements-setup.txt','requirements-build.txt']:
        pending.extend(line.strip() for line in (ROOT/file).read_text(encoding='utf-8').splitlines()
                       if line.strip() and not line.lstrip().startswith('#'))
    seen=set();rows=[];datas=[]
    while pending:
        req=Requirement(pending.pop())
        if req.marker is not None and not req.marker.evaluate():
            continue
        key=req.name.lower().replace('_','-')
        if key in seen:
            continue
        seen.add(key)
        dist=metadata.distribution(req.name)
        pending.extend(dist.requires or [])
        notices=[]
        for file in dist.files or []:
            if not any(word in str(file).lower() for word in ('license','copying','notice')):
                continue
            source=Path(dist.locate_file(file))
            if not source.is_file():
                continue
            relative=Path(*file.parts[1:])
            target=Path('third_party')/key/relative.parent
            datas.append((str(source),target.as_posix()))
            notices.append((target/source.name).as_posix())
        if key.startswith('winrt-'):
            notices.append('third_party/shared/PyWinRT-MIT.txt')
        if key in ('pyside6-essentials','shiboken6'):
            notices.extend(['third_party/shared/LGPL-3.0-only.txt','third_party/shared/GPL-3.0-only.txt'])
        if not notices:
            raise RuntimeError('배포 의존성 라이선스 원문 없음: '+dist.metadata['Name'])
        rows.append({'name':dist.metadata['Name'],'version':dist.version,
                     'license':dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                     'project_urls':dist.metadata.get_all('Project-URL') or [],'notices':notices})
    for file in (ROOT/'vendor/licenses').glob('*'):
        if file.is_file():datas.append((str(file),'third_party/shared'))
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python 라이선스 원문 없음')
    datas.append((str(python_license),'third_party/python'))
    folder=ROOT/'build';folder.mkdir(exist_ok=True)
    manifest=folder/'dependency-notices.json'
    manifest.write_text(json.dumps({'python':sys.version.split()[0],'dependencies':sorted(rows,key=lambda r:r['name'])},
                                   ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    datas.append((str(manifest),'third_party'))
    return datas
