# 기여 가이드

이 프로젝트는 AI와 대화하며 빠르게 만든 바이브 코딩 결과물입니다. 버그 제보·개선 제안·PR 모두 환영합니다.
아래는 헷갈리기 쉬운 규칙들입니다 — 몰라도 이슈는 자유롭게 올려 주세요, PR 낼 때만 참고하면 됩니다.

## 개발 환경

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

테스트:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

게임 자료(문구·아이콘)가 있어야 하는 테스트는 `setup.ps1`을 실제 게임이 설치된 PC에서 돌리기 전까지는
자동으로 건너뜁니다(`tests/__init__.py`의 `HAS_GAME_DATA`). CI(GitHub Actions)는 게임 파일이 없는
환경이라 이 테스트들은 항상 건너뜁니다.

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
- **커밋 메시지·코드 주석은 한국어**로 씁니다 (사용자층이 한국어 사용자라 UI 문구도 한국어뿐입니다).

## 구조에서 헷갈리는 것

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
