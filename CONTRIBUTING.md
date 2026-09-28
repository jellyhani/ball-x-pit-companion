# 기여 가이드

Windows용 비공식 BALL x PIT 도우미입니다. 버그 제보·개선 제안·PR을 환영합니다.
AI 코딩 도구를 사용해 바이브 코딩으로 만든 개인 프로젝트입니다.
기여 코드는 작성 방식과 관계없이 같은 검토·검증 기준을 적용합니다.
처음 읽을 때는 [구조와 코드 읽기 안내](docs/ARCHITECTURE.md), 배포할 때는
[릴리스 절차](docs/RELEASING.md)를 참고하세요.

기여자는 자신이 제출할 권한이 있는 코드·문서만 제공하고, 제3자 저작권 표시와 라이선스를 보존해야 합니다.
프로젝트의 원본 코드와 문서는 기존 MIT 라이선스에 따라 기여합니다. 게임 자료·세이브·비밀 정보는
PR과 이슈에 첨부하지 마세요. [권리 안내](DISCLAIMER.md)와 [개인정보 안내](PRIVACY.md)도 확인해 주세요.

## 개발 환경

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

테스트:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

게임 자료(문구·아이콘)가 있어야 하는 테스트는 `setup.ps1`을 실제 게임이 설치된 PC에서 돌리기 전까지는
자동으로 건너뜁니다(`tests/__init__.py`의 `HAS_GAME_DATA`). CI(GitHub Actions)는 게임 파일 없이
기본 계산과 Python 대체 계산을 각각 검사합니다. 게임 자료가 필요한 테스트는 로컬에서 확인해야 합니다.

네이티브 계산 없이 테스트하려면(파이썬 대체 경로 확인):

```powershell
$env:BXP_NO_NATIVE=1; .venv\Scripts\python.exe -m unittest discover -s tests -t .
```

## 꼭 지켜야 할 것

- **게임을 조작하지 않습니다.** 입력·클릭·게임 값 변경은 절대 안 됩니다. 연동 플러그인은 읽기 전용이고
  Harmony 패치를 쓰지 않습니다. 이 원칙을 벗어나는 PR은 받을 수 없습니다.
- **연동 플러그인 버전은 세 곳이 같아야 합니다**: `tools/bepinex/BallxPitBridge/Plugin.cs`의
  `Plugin.Version`, `BallxPitBridge.csproj`의 `<Version>`, `src/services/mod_installer.py`의
  `PLUGIN_VERSION`. 플러그인을 고치면 셋 다 올리고, `vendor/bepinex/BallxPitBridge.dll`도 다시 빌드해
  커밋하세요 (`tools\bepinex\bridge.ps1 build`).
- **`*.ps1`·`*.bat`은 ASCII + CRLF**로 유지합니다 (`.gitattributes`가 강제하지만, 에디터가 BOM 없는
  UTF-8 한글을 쓰면 Windows PowerShell 5.1이 깨진 글자로 읽습니다).
- **게임에서 추출한 자료(문구·아이콘·스크린샷)는 커밋하지 않습니다.** `.gitignore`가 대부분 막지만,
  새 파일을 추가할 땐 한 번 더 확인해 주세요. 저작권이 게임 개발사에 있습니다.
- **C++ (`native/bxp_native.cpp`)는 C 런타임·표준 헤더 없이 빌드합니다** (Windows SDK가 없는 환경 기준).
  `<cmath>`·`<vector>`·`emmintrin.h` 등은 쓸 수 없고, 필요한 내장 함수는 직접 선언합니다. 메모리는
  파이썬이 넘겨줍니다. 파이썬 구현(`harvest_sim.simulate_team_py`, `layout_opt`의 파이썬 담금질)과
  결과가 같아야 하고, `tests/test_native*.py`로 확인합니다. C++을 고치면 DLL도 다시 빌드해 커밋하세요
  (`native\build.ps1`).
- **가중치·수치에는 근거를 코드 주석으로 남깁니다.** 위키·커뮤니티 공략·실제 플레이 기록 중 어디서 온
  값인지 밝히고, 확실하지 않으면 "추정"이라고 씁니다. 출처는 README `자료 출처`에도 추가해 주세요.
- **커밋 메시지·코드 주석은 한국어**로 씁니다. UI는 한국어 원문과 15개 번역 파일로 16개 언어를 지원합니다.
  첫 실행 안내도 번역 대상이며, 게임이 추출한 원문과 도우미가 만든 안내를 구분합니다.

## 구조에서 헷갈리는 것

- 한 줄에는 한 동작을 적고, 긴 조건·JSON 구성·튜플은 의미 단위로 나눕니다. 물리 좌표에는 축·단위를,
  시간에는 게임 시간인지 실제 시간인지, 게임 레벨에는 0부터 시작하는지 주석으로 남깁니다.
- 주석은 코드 번역보다 **왜 이 규칙이 필요한지**를 설명합니다. 실제 getter 값·커뮤니티 평가·계산 근사를
  구분하고, 검증하지 않은 수치를 ‘정확함’ 또는 ‘최적’이라고 쓰지 않습니다.
- 순수 서식 변경과 동작 변경은 구분합니다. 이번 핵심 Python 모듈의 서식은 Ruff 0.16.9, 줄 길이 110으로
  정리했습니다. 관련 없는 파일 전체를 자동 수정하지 말고, 공개 함수명·JSON 필드·네이티브 ABI를 보존하세요.

- 게임 값은 연동 플러그인이 named pipe로 보내는 JSON (`src/tracking/bridge_adapter.py`).
- 무거운 계산(궤적 계산·배치 최적화)은 별도 프로세스(`SimWorker`)에서 돕니다 — UI 스레드에서 직접
  계산하지 않습니다.
- 배치 점수(`src/engine/layout_opt.py`의 `Scorer`)는 범위 효과 + 발사대 앞 구역 + 프리셋 가산으로
  이루어집니다. 화면에 보고하는 값(`effect_after`)은 범위 효과만이고, 실제 배치를 고르는 기준은
  앞 구역까지 포함합니다 — 테스트를 보면 헷갈리지 않습니다.

## 확인 방법

- 코드를 고친 뒤: 전체 테스트 + (네이티브 관련이면) `BXP_NO_NATIVE=1` 테스트 + 회귀 검사
  (`.venv\Scripts\python.exe tools\regress.py`, 의도한 변화면 `--update`).
- 오버레이 배치·HUD 모양은 `tests/test_overlay_place.py`처럼 오프스크린으로 그려 이미지로 확인할 수
  있습니다.
- 실제 게임 확인이 필요한 변경은 PR 설명에 "실기 확인 못 함"이라고 남겨 주세요 — 리뷰할 때 참고합니다.
