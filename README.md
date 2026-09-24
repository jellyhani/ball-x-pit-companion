# BALL x PIT 도우미 (비공식)

[BALL x PIT](https://store.steampowered.com/app/2062430/) 을 하는 동안 게임 위에 추천을 띄워 주는 Windows용 도우미입니다.
게임 값을 **읽기만** 하는 연동 플러그인(BepInEx)으로 실시간 상태를 받아 계산합니다.

> 비공식 팬 제작 도구입니다. 개발사·배급사와 관련이 없습니다. 게임 값·세이브·난수는 바꾸지 않지만,
> 모드를 쓰는 것이 게임 이용약관이나 순위표에 어떤 영향이 있는지는 확인하지 못했습니다. 사용 책임은 본인에게 있습니다.

## 현재 상태 — 초기 버전, 개선할 게 많습니다

이 프로젝트는 AI(Claude)와 대화하며 빠르게 만든 **바이브 코딩** 결과물입니다. 실제 게임으로 확인하며 만들었지만
한 사람의 PC와 플레이로만 확인했고, 추정으로 정한 값이 많습니다. 추천은 참고용으로만 봐 주세요.
버그 제보·개선 제안·PR 환영합니다.

알려진 한계:
- **확인 범위**: Windows 11 · 1920×1080 · 한국어 · Steam 판 게임 버전 1.301 에서만 확인. 다른 해상도·언어는 거의 확인 못 함.
  화면 문구는 한국어만 있습니다.
- **추정 값**: 배치 점수의 가중치, 재생 건물 효과량, 효과가 겹칠 때의 규칙, 유령의 집(수치 미확인이라 계산에서 뺌),
  금광 골드 추정(튕김 × 1.5)은 게임 값이 아니라 추정·커뮤니티 측정값입니다.
- **채집 궤적**: 실제 채집 기록 몇 번과 맞춘 수준입니다(궤적 7~11초 일치, 돌은 ±5).
- **추천 규칙**: 규칙 기반입니다. 캐릭터 궁합은 공식 캐릭터 설명에서 끌어낸 프로필(13명, 가중치는 추정), 공략 사이트의 볼·패시브 티어와 캐릭터별 추천 빌드(8명), 이 캐릭터로 한 내 런 기록(같은 항목을 가진 런 3번 이상일 때)으로 반영합니다. 티어는 한 사람·한 사이트의 의견이고 게임 버전에 따라 달라지므로 작은 가중치(비슷할 때 가르는 정도)로만 씁니다. 공략 수준의 빌드 운영(언제 무엇을 버릴지 등)은 아직 약합니다.
- **게임 업데이트**: 게임이 바뀌면 연동 플러그인을 다시 빌드해야 할 수 있습니다.
- **exe**: 서명이 없어 SmartScreen 경고가 뜨고, 백신이 오탐할 수 있습니다. 걱정되면 소스에서 직접 실행·빌드하세요.
- 코드도 빠르게 덧붙여 온 부분이 많아(특히 `src/app_controller.py`) 구조 정리가 필요합니다.

## 할 수 있는 것

- **강화 선택창 추천**: 카드마다 순위(1위·2위·3위)와 확신도(확실·추천·근소·근거 약함), 진화 재료 준비도,
  패시브의 실제 수치 변화(예: 받는 피해 감소 10% → 20%), 덱 계열(화상·빙결·출혈·범위 …), 캐릭터 궁합(예: 밤그림자는 치명타,
  곡예사는 범위 피해, 살금발이는 생존 패시브)과 이 캐릭터로 한 내 기록, 새로고침·삭제 판단.
- **융합 화면 추천**, 보스 뒤 원정 계속/복귀 판단, 초당 피해 창.
- **기지 채집 조준**: 작업자 궤적 계산(실제 채집 기록과 맞춤)으로 추천 각도·예상 채집량을 게임 위에 그림.
  미완성 건물 먼저, 닿지 않으면 길을 여는 방법.
- **최적 배치**: 건물마다 효과(생산 건물·거처·대저택·대위 막사·잔병의 오두막·강철 요새·수도원 …)를 계산해
  기지를 다시 놓는 최적 배치와 옮기는 순서, 재배치 모드에서 게임 위 번호 안내. 공략 프리셋 '금광 U자'.
- **더 지을 것**: 설계도 건물·농장·채석장·자원 타일·금광의 추천 자리와 늘어나는 효과, 스파 재채집 손익.

## 설치 (exe, 파이썬 필요 없음)

1. [Releases](../../releases) 에서 `BallxPitCompanion-*.zip` 을 받아 원하는 폴더에 풉니다.
2. `BallxPitCompanion.exe` 를 실행합니다. 처음 한 번 **내 PC의 게임 파일에서** 게임 문구·아이콘을 추출합니다(10초 안팎).
3. 게임이 꺼져 있을 때 연동 모드(BepInEx + 연동 플러그인)를 자동으로 설치합니다. 게임이 켜져 있으면 게임 종료를 기다렸다 설치합니다.

## 설치 (소스에서)

필요한 것: Windows 10/11, [uv](https://docs.astral.sh/uv/) (`winget install --id=astral-sh.uv -e`), Steam 판 BALL x PIT.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` 이 하는 일:
1. Python 환경을 만듭니다 (`.venv`, 고정 버전 의존성).
2. **내 PC의 게임 파일에서** 게임 문구·아이콘을 추출합니다 → `%LOCALAPPDATA%\BallxPitCompanion\gamedata`.
   게임 자료는 저작권이 게임에 있으므로 이 저장소에 넣지 않습니다. 게임이 업데이트되면 `tools\setup_data.py` 를 다시 실행하세요.
3. 게임이 꺼져 있으면 연동 모드를 설치합니다: [BepInEx](https://github.com/BepInEx/BepInEx) 6 be.788 을
   [공식 빌드 서버](https://builds.bepinex.dev/projects/bepinex_be)에서 받아 SHA-256 을 확인한 뒤 게임 폴더에 풀고,
   이 저장소의 연동 플러그인(`vendor/bepinex/BallxPitBridge.dll`, 소스 `tools/bepinex/BallxPitBridge`)을 넣습니다.
   게임이 켜져 있으면 도우미가 게임 종료를 기다렸다 설치합니다.

실행: `run_overlay.bat` (게임도 함께 켜려면 `play.bat`), 종료: `stop_overlay.bat`. 설정 창은 F10.

연동 모드 끄기·지우기: `tools\bepinex\bridge.ps1 disable` / `uninstall` (이 도구가 추가한 파일만 지움).

## 개인정보·보안

- 인터넷 접속은 연동 모드 설치 때 BepInEx 공식 빌드 서버에서 받는 것뿐입니다(SHA-256 확인). 그 밖에 아무것도 보내지 않습니다.
- 전역 입력 감시: 단축키(F7~F10)와 **왼쪽 클릭의 좌표·시각**만 봅니다(선택 결과 판별용, 메모리에 최근 32개). 글자 키는 보지 않고 아무것도 파일로 남기지 않습니다.
- 로그·기록은 `%LOCALAPPDATA%\BallxPitCompanion` 에만 저장합니다. 게임 화면 저장은 설정 창에서 누를 때만 합니다.
- Windows 시작 시 실행은 설정 창에서 켤 때만 레지스트리(현재 사용자 Run)에 등록합니다.

## 동작 방식

- 연동 플러그인은 게임 메인 스레드에서 0.1~0.2초마다 값을 읽어 named pipe 로 보냅니다. Harmony 패치를 쓰지 않고
  게임 값을 바꾸지 않습니다. 게임 쪽 읽기 비용은 설정 창 진단 탭에 표시됩니다(보통 1ms 미만).
- 궤적·배치 계산은 별도 프로세스에서 돌아 화면이 끊기지 않습니다.
- 연동 없이도 화면 인식(Windows OCR + 아이콘 비교)으로 강화 선택창 추천은 동작합니다.

## exe 빌드

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```
PyInstaller onedir 빌드(Qt 라이브러리는 LGPL 조건대로 별도 파일), 테스트 통과 후에만 빌드, `dist\` 에 exe 와 zip.

## 개발

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

게임 자료가 필요한 테스트는 자료를 추출하기 전에는 건너뜁니다. 플러그인 빌드: `tools\bepinex\bridge.ps1 build`
(.NET SDK, 게임에서 BepInEx 를 한 번 실행해 만들어진 interop 필요).

## 자료 출처

게임 규칙·수치 중 게임에서 직접 읽지 못한 것은 아래 공개 자료를 참고해 **사실만** 옮겼습니다(글·데이터 복사 없음).
- [BALL x PIT Wiki (wiki.gg)](https://ballxpit.wiki.gg/wiki/Buildings) — 거처 효과는 캐릭터 레벨 4부터, 수도원 튕김당 +0.07초 등
- Steam 가이드 [My Optimized Town Layout](https://steamcommunity.com/sharedfiles/filedetails/?id=3610566537),
  [100% Utilization Base Layout](https://steamcommunity.com/sharedfiles/filedetails/?id=3602389407) — 생산량 측정값, 배치 권장
- 커뮤니티 평가(`data/community.json`, 순위·이름만 정리): [Game Rant 볼 진화 티어](https://gamerant.com/ball-x-pit-evolutions-tier-list-best-balls/) (2026-06),
  [Dexerto 패시브 티어](https://www.dexerto.com/wikis/ball-x-pit/passive-tier-list/) (2026-08), Dexerto 캐릭터별 추천 빌드
- [StonedModder/BallxPitxApp](https://github.com/StonedModder/BallxPitxApp) 연구 문서 — 생산 주기·타일 용량·건설 비용,
  빌드 아키타입 아이디어

## 라이선스

소스 코드는 [MIT](LICENSE). 서드파티 구성 요소는 [NOTICE.md](NOTICE.md) 를 보세요.
