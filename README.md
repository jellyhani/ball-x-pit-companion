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
- **확인 범위**: Windows 11 · 1920×1080 · 한국어 · Steam 판 게임 버전 1.301 에서만 확인. 다른 해상도는 거의 확인 못 함.
- **게임 문구 언어**: 게임 파일에서 이름·설명을 추출할 때 윈도우 UI 언어를 자동 감지해 그 언어로 가져옵니다
  (게임이 지원하는 16개 언어 중 하나 — 지원 안 하면 영어). 도우미 자체 화면은 한국어와 추가 15개 언어를 지원합니다.
- **추정 값**: 배치 점수의 가중치, 재생 건물 효과량, 효과가 겹칠 때의 규칙, 유령의 집(수치 미확인이라 계산에서 뺌),
  금광 골드 추정(튕김 × 1.5)은 게임 값이 아니라 추정·커뮤니티 측정값입니다.
- **채집 궤적**: 사건 발생 시각과 남은 시간을 기준으로 계산하며, 첫 작업자의 예상 경로를 표시합니다.
  기본은 현재 조준 3회·추천 1회 반사까지이며, Shift를 누르는 동안 둘 다 최대 7회까지 펼쳐집니다.
  설정 → 표시 → 조준 경로 길이에서 현재 조준을 짧게(1회)·보통(3회)·길게(7회)로 고를 수 있습니다.
  첫 구간은 실선, 이후는 흐려지는 점선이고 점은 예상 반사 위치입니다. 먼 경로는 실제와 다를 수 있습니다.
  자원별 1회 채집량(1+강화 레벨), 시간 보너스(강화 값×0.2초, 캐릭터·자원별 최대 20회), 긴 낫 범위,
  속도·관통·공사 점수 및 시작 채집 시간을 반영합니다. 게임의 매 프레임 원형 겹침 판정을 연속 이동으로
  근사하므로 모서리·동시 접촉에서 실제 수확량과 다를 수 있습니다. 이 예상 수확량은 배치 선택 점수에서 제외합니다.
- **추천 규칙**: 규칙 기반입니다. 공식 캐릭터 설명에서 끌어낸 성향(가중치는 추정), 공략 사이트의 볼·패시브 티어와 캐릭터별 추천 빌드, 이 캐릭터로 한 내 런 기록(같은 항목을 가진 런 3번 이상일 때)을 반영합니다. 티어는 작성자의 의견이며 게임 버전에 따라 달라집니다. 고정한 목표의 유효한 재료를 우선하고, 체력이 낮을 때는 생존 선택을 예외로 둡니다. 실제 승률을 검증한 추천은 아닙니다.
- **게임 업데이트**: 게임이 바뀌면 연동 플러그인을 다시 빌드해야 할 수 있습니다.
- **exe**: 서명이 없어 SmartScreen 경고가 뜨고, 백신이 오탐할 수 있습니다. 걱정되면 소스에서 직접 실행·빌드하세요.
- 코드도 빠르게 덧붙여 온 부분이 많아(특히 `src/app_controller.py`) 구조 정리가 필요합니다.

## 할 수 있는 것

- **강화 선택창 추천**: 카드마다 순위(1위·2위·3위)와 확신도(확실·추천·근소·근거 약함), 진화 재료 준비도,
  패시브의 실제 수치 변화(예: 받는 피해 감소 10% → 20%), 덱 계열(화상·빙결·출혈·범위 …), 캐릭터 궁합(예: 밤그림자는 치명타,
  곡예사는 범위 피해, 살금발이는 생존 패시브)과 이 캐릭터로 한 내 기록, 새로고침·삭제 판단.
- **융합 화면 추천**(진화 결과의 커뮤니티 티어 반영), 보스 뒤 원정 계속/복귀 판단, 초당 피해 창.
- **백과사전 해금 모드**: 설정 → 표시에서 별도 켜기/끄기(기본 꺼짐). 실제 게임의 발견 횟수와 융합 기록으로
  미발견 상위 볼·미기록 융합 쌍을 찾고, 현재 보유 볼에서 이어지는 레벨업 선택과 융합 후보를 우선합니다.
  잠긴 재료·삭제한 볼·슬롯 부족을 거르고, 기록이 없으면 미해금으로 추정하지 않습니다. 다단계 진화는 재료 경로를
  전개하되 현재 빈 슬롯으로 필요한 재료를 담을 수 있는 보수적인 경로를 대상으로 합니다.
- **캐릭터 조합 추천**: 알선소가 있으면 캐릭터 선택 화면에서 두 캐릭터 조합을 추천 (커뮤니티 추천 + 내 런 기록).
- **기지 채집 조준**: 작업자 궤적 계산(실제 채집 기록과 맞춤)으로 추천 각도·예상 채집량을 게임 위에 그림.
  미완성 건물 먼저, 닿지 않으면 길을 여는 방법.
- **배치 개선안**: 현재 배치에서 핵심 범위 효과·가동 생산·입구를 보존하며 적게 옮길 후보를 찾습니다.
  실제로 끝까지 실행할 수 있는 순서를 확인하고 재배치 모드에서 다음 위치를 표시합니다. 최적 배치의 증명은 아닙니다.
- **더 지을 것**: 게임의 건설 가능 목록·크기·비용으로 후보를 만들며, 고급 자원 타일을 우선합니다.
  금광은 사용자 정책상 제외하고, 공략 핵심·일반 후보·후순위를 구분합니다. 스파는 부족한 자원을 실제 기록에서 얻는 경우만 보충 후보로 봅니다.

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

연동 모드 끄기·지우기: `tools\bepinex\bridge.ps1 disable` / `uninstall`.
`uninstall`은 도우미의 `BallxPitBridge.dll`만 제거하며 공유 BepInEx·다른 모드·설정·로그는 남깁니다.
`disable`은 BepInEx 전체를 끄므로 다른 BepInEx 모드에도 적용됩니다.

## 개인정보·보안

- 인터넷 접속은 연동 모드 설치 때 BepInEx 공식 빌드 서버에서 받는 것뿐입니다(SHA-256 확인). 그 밖에 아무것도 보내지 않습니다.
- 전역 입력 감시: 단축키(F7~F10)·Shift 눌림 상태와 **왼쪽 클릭의 좌표·시각**만 봅니다(선택 결과 판별용, 메모리에 최근 32개). 글자 키는 보지 않고 아무것도 파일로 남기지 않습니다.
- 로그·기록은 `%LOCALAPPDATA%\BallxPitCompanion` 에만 저장합니다. 게임 화면 저장은 설정 창에서 누를 때만 합니다.
- Windows 시작 시 실행은 설정 창에서 켤 때만 레지스트리(현재 사용자 Run)에 등록합니다.

## 동작 방식

- 연동 플러그인은 게임 메인 스레드에서 0.1~0.2초마다 값을 읽어 named pipe 로 보냅니다. Harmony 패치를 쓰지 않고
  게임 값을 바꾸지 않습니다. 게임 쪽 읽기 비용은 설정 창 진단 탭에 표시됩니다(보통 1ms 미만).
- 궤적·배치 계산은 별도 프로세스에서 돌아 화면이 끊기지 않습니다.
- 채집 궤적과 배치 최적화는 C++ 모듈(`native/bxp_native.cpp` → `src/engine/bxp_native.dll`, C 런타임 없이 빌드한 DLL)로
  계산합니다. 배치는 시작점을 바꿔 가며 탐색하며 후보 준비에도 시간 예산을 적용합니다.
  마지막 이동 가능 여부·생산 보존·공사 접근 검사는 별도로 수행하므로 전체 완료 시간은 기지와 작업자 수에 따라 달라집니다.
  궤적 결과·배치 점수는 파이썬 구현과 같고(`tests/test_native*.py`), DLL 이 없으면 파이썬으로 계산합니다.
- 연동 없이도 화면 인식(Windows OCR + 아이콘 비교)으로 강화 선택창 추천은 동작합니다.

## exe 빌드

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```
PyInstaller onedir 빌드(Qt 라이브러리는 LGPL 조건대로 별도 파일), 테스트 통과 후에만 빌드, `dist\` 에 exe 와 zip.
실행 파일도 격리된 자료 폴더·합성 파이프로 시작, 별도 계산 프로세스, 정상 종료를 확인한 뒤 zip으로 묶습니다.
빌드 시 외부 도구의 DLL 검색 경로를 제한하여 Qt의 시스템 ICU 대신 다른 프로그램의 동명 DLL이 포함되는 것을 막습니다.

## 개발

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

게임 자료가 필요한 테스트는 자료를 추출하기 전에는 건너뜁니다. 네이티브 모듈 빌드: `nativeuild.ps1`
(Visual Studio C++ 도구, Windows SDK 불필요). 플러그인 빌드: `tools\bepinex\bridge.ps1 build`
(.NET SDK, 게임에서 BepInEx 를 한 번 실행해 만들어진 interop 필요).

## 자료 출처

게임 규칙·공략의 선택 기준은 아래 자료를 참고합니다. 직접 읽은 게임 값, 공략의 의견, 검증 중인 모형 가중치를 구분합니다.

- **채집·백과사전 원본 확인**: Steam 빌드 23150541, 게임 1.301의 `GameAssembly.dll`과
  BepInEx가 생성한 메서드 주소 표를 읽었습니다. 채집량은 `BuildingInst.Harvest`(RVA 0x464990),
  시간 보너스는 `BaseGridMgr.WorkerHarvestResource`(0x4552C0), 20회 제한은 `BaseMgr.IncreaseHarvestClock`(0x457E30),
  시작 시간·참가자는 `BaseMgr.InitWorkerMode`(0x458670)·`SetUpActiveWorkers`(0x45D090),
  속도·범위는 `BallObj.InitWorker`(0x5B7360)에서 확인했습니다. 발견 여부는 게임 백과사전과 같은
  `HeroMetaStats.NumObtained`, 융합 이력은 `NumCombos`, 발사 순서는 `MetaSaveData.CharWorkerOrder`를 읽습니다.
  원본 게임 파일·추출한 코드·개인 해금 기록은 저장소에 포함하지 않습니다.
- [BALL x PIT Wiki (wiki.gg)](https://ballxpit.wiki.gg/wiki/Buildings) — 거처 효과는 캐릭터 레벨 4부터, 수도원 튕김당 +0.07초 등
- Steam 가이드 [My Optimized Town Layout](https://steamcommunity.com/sharedfiles/filedetails/?id=3610566537),
  [100% Utilization Base Layout](https://steamcommunity.com/sharedfiles/filedetails/?id=3602389407),
  [Base Layout (Naturalist Update)](https://steamcommunity.com/sharedfiles/filedetails/?id=3796785943) — 생산량 측정값,
  목표 자원 비율(밀 1.5 : 나무 1.25 : 돌 1), 대위 막사·잔병의 오두막·강철 요새 범위, 채집 거처는 자원 옆에,
  전쟁 회의실·채집가의 오두막은 안 지음
  - 건설 조언: 이 공략들은 완성형 배치 설계이며 전체 건물의 건설 순위표는 아닙니다. 현재 대상 건물이 있는
    잔병의 오두막·대위 막사·강철 요새의 신규 건설을 같은 핵심 그룹으로, 대저택을 후순위로 분류합니다.
    일반 후보에는 공략 우선순위가 없다고 표시하고, 그 비용을 확정된 건설 목표처럼 채집 수요에 더하지 않습니다.
    금광 제외는 사용자 선택이며, 전쟁 회의실·채집가의 오두막 제외는 채택한 공략의 선택입니다.
  - 재배치: 현재 배치에서 적게 옮기는 `layout_guide` 경로가 작동합니다. 기존 허브 대상·가동 생산 영역·입구를
    보존하고, 게임의 현재 범위 계수와 일치하는 자원 거처의 빈 범위를 개선하는 후보를 함께 비교합니다.
    무한 강화 건물은 요새와 막사, 능력치 거처는 오두막과 막사 양쪽에 포함합니다.
    `layout_city.plan_city`의 전체 재설계는 실행 중인 기본 추천 경로가 아닙니다.
  - 해금·비용·크기·현재 범위는 게임 연동값입니다. 후보 위치의 범위 판정과 채집량은 도우미 계산이며,
    범위 효과 점수에는 탐색용 가중치가 포함됩니다. 최적 배치·실제 시간당 수익을 뜻하지 않습니다.
    공략의 생산 시설 개수·평균 수익을 현재 기지에 그대로 대입하지 않으며, apo가 일부 채집 거처를 희생한
    것처럼 모든 공략의 조건을 동시에 만족한다고 보장하지 않습니다.
  - 이동 순서를 끝까지 만들 수 없으면 현재 배치로 평가를 되돌리고 구매 안내를 보류합니다.
    길 열기 후의 한 배치에서 그림·범위 점수·각도표를 함께 계산하며, 실제 조준 한계 밖의 각도는 사용하지 않습니다.
    같은 종류라도 용량·범위·공사 상태가 다른 건물은 서로 바꿔도 되는 동일 건물로 취급하지 않습니다.
- 진화표: Steam 가이드 [Ultimate Guide of Evolutions](https://steamcommunity.com/sharedfiles/filedetails/?id=3749789365)
  (게임에서 읽은 레시피와 개수 대조용)
- 커뮤니티 평가(`data/community.json`, 순위·이름만 정리): [Game Rant 볼 진화 티어](https://gamerant.com/ball-x-pit-evolutions-tier-list-best-balls/) (2026-06),
  [Dexerto 패시브 티어](https://www.dexerto.com/wikis/ball-x-pit/passive-tier-list/) (2026-08), Dexerto 캐릭터별 추천 빌드,
  캐릭터 조합: [Dexerto](https://www.dexerto.com/wikis/ball-x-pit/best-ball-x-pit-character-combinations/), [Screen Rant](https://screenrant.com/ball-x-pit-best-character-combinations-matchmaker/), [Steam 토론](https://steamcommunity.com/app/2062430/discussions/0/624436409753066508/)
- 캐릭터 성향(`data/rules.json` characters.*.strategy — 추천 가중치는 추정): 게임 파일의 캐릭터 공식 설명,
  [BALL x PIT Wiki 캐릭터·시작 볼](https://ballxpit.wiki.gg/wiki/Characters), [나무위키 캐릭터](https://namu.wiki/w/BALL%20x%20PIT/%EC%BA%90%EB%A6%AD%ED%84%B0),
  Dexerto 캐릭터별 추천 빌드·[캐릭터 티어](https://www.dexerto.com/wikis/ball-x-pit/character-tier-list-2/),
  [TheGamer 캐릭터 순위](https://www.thegamer.com/ball-x-pit-best-characters-tier-list/),
  Steam 가이드 [Character Combinations](https://steamcommunity.com/sharedfiles/filedetails/?id=3600044840)
- 기지 배치 공략: [Screen Rant](https://screenrant.com/ball-x-pit-base-layout-harvest-tips/) (공사 중인 건물을 채집 구역 가장자리로,
  잔병의 오두막 범위에 거처, 대저택 범위 채우기), [Dexerto](https://www.dexerto.com/wikis/ball-x-pit/best-base-layout/),
  위키 [Veteran's Hut](https://ballxpit.wiki.gg/wiki/Veteran's_Hut)
  (레벨 4·7·9 에서 20·25·30%), [Iron Fortress](https://ballxpit.wiki.gg/wiki/Iron_Fortress) (근처 공사장 건설 점수 +4),
  Steam 토론 [Max Iron Fortress + Veterans Hut + Captains Quarters](https://steamcommunity.com/app/2062430/discussions/0/595163560549778862/)
- [StonedModder/BallxPitxApp](https://github.com/StonedModder/BallxPitxApp) 연구 문서 — 생산 주기·타일 용량·건설 비용,
  빌드 아키타입 아이디어
- 퓨전 리액터(융합/분열) 우선순위: [dood.gg 메타 가이드](https://dood.gg/en/ball-x-pit/guides/meta) — 초반엔 분열로 볼을
  먼저 레벨업하고, 재료가 갖춰지면 그때 융합·진화를 노리라는 조언

## 라이선스

소스 코드는 [MIT](LICENSE). 서드파티 구성 요소는 [NOTICE.md](NOTICE.md) 를 보세요.
