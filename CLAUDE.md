# BALL x PIT 도우미 — 작업 규칙

Windows 전용 게임 오버레이. 파이썬(PySide6) 앱 + BepInEx 연동 플러그인(C#) + 계산용 C++ DLL.
사용자와의 대화·화면 문구·커밋 메시지·코드 주석은 한국어.

## 명령
- 테스트: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
- 네이티브 끄고 테스트 (파이썬 대체 경로): `BXP_NO_NATIVE=1` 을 붙여 같은 명령
- 추천 회귀 검사: `.venv\Scripts\python.exe tools\regress.py` (바뀐 게 맞으면 `--update`)
- 네이티브 DLL: `powershell -ExecutionPolicy Bypass -File native\build.ps1`
- 연동 플러그인: `powershell -ExecutionPolicy Bypass -File tools\bepinex\bridge.ps1 build`
- exe: 실행 중인 앱을 먼저 끈다 `dist\BallxPitCompanion\BallxPitCompanion.exe --stop` → `build_exe.ps1`
  (테스트가 통과해야 빌드. 앱이 켜져 있으면 파일 잠김·부하로 실패)

## 화면 문구 번역
- 사용자에게 보이는 문구는 `tr("한국어 원문", 이름=값)` 으로 감싼다 (`src/i18n.py`, 원문이 키). 번역은 `data/i18n/{en,ja,schinese,tchinese}.json`.
  새 문구를 넣으면 네 파일에 모두 추가 (`tests/test_i18n.py` 가 빠진 키·자리표시자 불일치를 잡음). 로그·docstring 은 한국어 그대로.
- 비교·분기에 쓰는 값(런 결과 '보스 격퇴', 화면 판별 문구, 게임 원문 매칭 낱말)은 번역하지 않는다. 확인용: `BXP_LANG=en` 으로 실행·테스트.

## 꼭 지킬 것
- IMPORTANT: 게임을 조작하지 않는다 (입력·클릭·게임 값 변경 금지). 플러그인은 읽기 전용, Harmony 패치 없음.
- 플러그인 버전은 세 곳이 같아야 한다: `Plugin.cs` Plugin.Version, `BallxPitBridge.csproj` Version,
  `src/services/mod_installer.py` PLUGIN_VERSION. 바꾸면 `vendor/bepinex/BallxPitBridge.dll` 도 다시 빌드해 커밋.
- `*.ps1`·`*.bat` 은 ASCII + CRLF (Windows PowerShell 5.1 이 BOM 없는 UTF-8 한글을 잘못 읽는다).
- 게임에서 추출한 자료(문구·아이콘)는 저장소에 넣지 않는다 (`.gitignore`). 공개 예정 저장소라 개인 경로·계정 정보 금지.
- C++ (`native/bxp_native.cpp`): C 런타임·표준 헤더 없이 빌드한다 (이 PC 에 Windows SDK 없음).
  `<cmath>`·`<vector>`·`emmintrin.h` 금지 — 필요한 내장 함수는 직접 선언. 메모리는 파이썬이 넘긴다.
  파이썬 구현(`harvest_sim.simulate_team_py`, `layout_opt` 의 파이썬 담금질)과 결과가 같아야 한다 → `tests/test_native*.py`.
  C++ 를 고치면 DLL 을 다시 빌드해 커밋 (DLL 없으면 앱은 파이썬으로 계산).

## 구조에서 헷갈리는 것
- 게임 값은 플러그인이 named pipe 로 보내는 JSON (`src/tracking/bridge_adapter.py`). 화면 좌표는 게임 창 기준 →
  `frame.to_screen`, 논리 좌표는 `geometry.phys_to_logical_rect`.
- 창이 밀려 들어오는 애니메이션 중에는 UI 위치 값이 화면 밖이다 → `_rect(v, size)` 가 버린다. 첫 프레임 값에 의존하지 말 것.
- 무거운 계산은 `SimWorker` (별도 프로세스, 채널마다 최신 요청만). UI 스레드에서 계산하지 않는다.
- 배치 점수: `layout_opt.Scorer` (범위 효과 + 발사대 앞 구역 + 프리셋). 보고하는 `effect_after` 는 범위 효과만,
  배치를 고르는 기준은 앞 구역 포함 — 테스트는 선택 기준으로 비교한다.
- 가중치·수치에는 근거(위키·커뮤니티·실제 기록)를 주석으로 남긴다. 출처는 README '자료 출처'에도.

## 확인 방법
- 코드 변경 뒤: 전체 테스트 + (네이티브 관련이면) `BXP_NO_NATIVE=1` 테스트 + regress.
- 오버레이 배치·HUD 모양: `tests/test_overlay_place.py`, HUD 는 오프스크린으로 그려 이미지로 확인할 수 있다.
- 실제 게임 확인이 필요한 것은 사용자에게 무엇을 봐 달라고 구체적으로 요청한다 (스크린샷).
