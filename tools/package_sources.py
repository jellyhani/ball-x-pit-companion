"""번들에 포함한 LGPL 라이브러리의 정확한 버전 소스를 별도 릴리스 자산으로 준비한다.

공식 배포 서버와 게시된 SHA-256을 vendor/licenses/corresponding-sources.json에 고정한다.
캐시를 재사용하더라도 해시를 확인하며 게임 자료는 이 도구의 입력이 아니다.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "vendor/licenses/corresponding-sources.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_versions(manifest: dict, bundle_dir: Path | None = None) -> None:
    """소스 목록이 현재 wheel·실제 Qt 바이너리를 따라가는지 확인한다."""
    from PySide6.QtCore import qVersion

    if manifest.get("schema") != 1 or manifest.get("qt_version") != qVersion():
        raise ValueError("Qt 버전과 대응 소스 목록이 다릅니다")
    covered = set()
    for library in manifest["libraries"]:
        for package, expected in library["packages"].items():
            if metadata.version(package) != expected:
                raise ValueError(f"대응 소스 버전 불일치: {package}")
        covered.update(library["covers"])
    if bundle_dir is not None:
        actual = {path.name for path in bundle_dir.rglob("Qt6*.dll")}
        if not actual or not actual <= covered:
            raise ValueError(f"대응 소스 목록에 없는 Qt 모듈: {sorted(actual - covered)}")


def source_file(library: dict, cache_dir: Path, *, download: bool) -> Path:
    """소스 아카이브를 확보한다. 확인하지 않은 다운로드는 최종 파일명으로 놓지 않는다."""
    filename = library["archive"]
    expected = library["sha256"]
    if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("잘못된 소스 아카이브 이름 또는 해시")
    target = cache_dir / filename
    if target.exists():
        if sha256(target) != expected:
            raise ValueError(f"기존 소스 캐시의 해시 불일치: {filename}")
        return target
    if not download:
        raise FileNotFoundError(f"대응 소스가 없습니다. --download로 준비하세요: {filename}")
    url = library["url"]
    if not url.startswith(("https://download.qt.io/", "https://files.pythonhosted.org/")):
        raise ValueError("검토한 공식 소스 배포 서버가 아닙니다")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".part", delete=False) as stream:
        pending = Path(stream.name)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, pending.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if sha256(pending) != expected:
            raise ValueError(f"소스 다운로드 해시 불일치: {filename}")
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
    return target


def create_bundle(manifest: dict, sources: list[Path], output: Path) -> None:
    """소스 원본과 입수 근거를 함께 묶는다. 아카이브는 이미 압축돼 있으므로 재압축하지 않는다."""
    if len(sources) != len(manifest["libraries"]):
        raise ValueError("대응 소스 목록과 파일 수가 다릅니다")
    for library, source in zip(manifest["libraries"], sources):
        if source.name != library["archive"] or sha256(source) != library["sha256"]:
            raise ValueError("번들 작성 직전 소스 검증 실패")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("corresponding-sources.json", json.dumps(manifest, indent=2) + "\n")
        for source in sources:
            bundle.write(source, source.name)
        for filename in ("LICENSE", "README.md", "NOTICE.md", "DISCLAIMER.md", "PRIVACY.md"):
            bundle.write(ROOT / filename, filename)
        for document in sorted((ROOT / "docs").rglob("*.md")):
            bundle.write(document, document.relative_to(ROOT).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="누락된 공식 소스를 내려받습니다")
    parser.add_argument("--bundle-dir", type=Path, help="실행 파일 폴더의 Qt 모듈까지 대조합니다")
    arguments = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from src.version import APP_VERSION

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    validate_versions(manifest, arguments.bundle_dir)
    cache = ROOT / "build/dependency-sources"
    sources = [source_file(row, cache, download=arguments.download) for row in manifest["libraries"]]
    output = ROOT / "dist" / f"BallxPitCompanion-{APP_VERSION}-dependency-sources.zip"
    create_bundle(manifest, sources, output)
    print(json.dumps({"archive": output.name, "sha256": sha256(output), "bytes": output.stat().st_size}))


if __name__ == "__main__":
    main()
