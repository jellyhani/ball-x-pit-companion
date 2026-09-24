# BALL x PIT 도우미 (비공식)

[BALL x PIT](https://store.steampowered.com/app/2062430/) 을 하는 동안 게임 위에 추천을 띄워 주는 Windows용 도우미입니다.
게임 값을 **읽기만** 하는 연동 플러그인(BepInEx)으로 실시간 상태를 받아 계산합니다.

> 비공식 팬 제작 도구입니다. 개발사·배급사와 관련이 없습니다. 게임 값·세이브·난수는 바꾸지 않지만,
> 모드를 쓰는 것이 게임 이용약관이나 순위표에 어떤 영향이 있는지는 확인하지 못했습니다. 사용 책임은 본인에게 있습니다.

## 할 수 있는 것

- **강화 선택창 추천**: 카드마다 순위(1위·2위·3위)와 확신도(확실·추천·근소·근거 약함), 진화 재료 준비도,
  패시브의 실제 수치 변화(예: 받는 피해 감소 10% → 20%), 덱 계열(화상·빙결·출혈·범위 …), 새로고침·삭제 판단.
- **융합 화면 추천**, 보스 뒤 원정 계속/복귀 판단, 초당 피해 창.
- **기지 채집 조준**: 작업자 궤적 계산(실제 채집 기록과 맞춤)으로 추천 각도·예상 채집량을 게임 위에 그림.
  미완성 건물 먼저, 닿지 않으면 길을 여는 방법.
- **최적 배치**: 건물마다 효과(생산 건물·거처·대저택·대위 막사·잔병의 오두막·강철 요새·수도원 …)를 계산해
  기지를 다시 놓는 최적 배치와 옮기는 순서, 재배치 모드에서 게임 위 번호 안내. 공략 프리셋 '금광 U자'.
- **더 지을 것**: 설계도 건물·농장·채석장·자원 타일·금광의 추천 자리와 늘어나는 효과, 스파 재채집 손익.

## 설치

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

## 동작 방식

- 연동 플러그인은 게임 메인 스레드에서 0.1~0.2초마다 값을 읽어 named pipe 로 보냅니다. Harmony 패치를 쓰지 않고
  게임 값을 바꾸지 않습니다. 게임 쪽 읽기 비용은 설정 창 진단 탭에 표시됩니다(보통 1ms 미만).
- 궤적·배치 계산은 별도 프로세스에서 돌아 화면이 끊기지 않습니다.
- 연동 없이도 화면 인식(Windows OCR + 아이콘 비교)으로 강화 선택창 추천은 동작합니다.

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
- [StonedModder/BallxPitxApp](https://github.com/StonedModder/BallxPitxApp) 연구 문서 — 생산 주기·타일 용량·건설 비용,
  빌드 아키타입 아이디어

## 라이선스

소스 코드는 [MIT](LICENSE). 서드파티 구성 요소는 [NOTICE.md](NOTICE.md) 를 보세요.
