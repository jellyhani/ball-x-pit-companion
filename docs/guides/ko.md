# BALL x PIT Companion — 한국어

[🌐 Languages](../../README.md#choose-your-language)

BALL x PIT의 비공식 Windows 도우미입니다. 게임 상태를 읽어 강화 선택·융합·백과사전 해금·채집·기지 배치를 안내합니다. 게임 조작은 사용자가 직접 합니다.

## 설치

Windows 10/11과 본인이 설치한 Steam판 BALL x PIT이 필요합니다. [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases)에 ZIP이 게시돼 있으면 전체를 풀고 `BallxPitCompanion.exe`를 실행하세요. `_internal` 폴더도 함께 있어야 합니다. 릴리스가 없으면 아래 소스 설치를 이용하세요. 서명되지 않은 파일이라 SmartScreen 경고가 나올 수 있습니다.

소스 설치는 저장소를 받은 뒤 해당 폴더에서 PowerShell을 열고 uv 설치와 초기 설정을 실행합니다.

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

이후 `run_overlay.bat`를 실행합니다. 초기 설정은 의존성과 BepInEx를 받고 본인의 게임에서 문구·아이콘을 추출합니다. 연동 설치·갱신 때는 게임을 정상 종료하세요. 게임 실행 중에는 설치가 대기합니다.

## 사용 방법

- 설정에서 게임 연결 상태를 확인하고 강화·융합 화면을 열면 추천이 표시됩니다.
- 해금을 우선하려면 표시 설정의 백과사전 모드를 켜세요. 기본값은 꺼짐입니다.
- 현재 배치와 가이드 배치를 비교하고 안내 순서대로 직접 옮기세요.
- 채집 경로와 수확량은 예상입니다. 여러 각도에서 접근할 수 있다는 것이 한 번에 전부 수확한다는 뜻은 아닙니다.

## 개인정보와 한계

연동은 읽기 전용이며 Harmony 패치, 세이브 변경, 게임 입력을 하지 않습니다. 로그·추출 자료는 `%LOCALAPPDATA%\BallxPitCompanion`에 저장하고 플레이 기록을 업로드하지 않습니다. 주로 Windows 11·1920×1080·한국어·게임 1.301에서 확인한 초기 버전입니다. 최적 배치나 정확한 미래 DPS를 보장하지 않습니다. 모든 번역의 원어민 검수가 끝난 것은 아닙니다. 공식 제품이 아닙니다.

## 문제 해결·제보

오버레이가 안 보이면 표시 설정·게임 창·연동 상태를 확인하세요. [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues)에 게임/도우미 버전, 언어, 해상도, 재현 순서, 기대 결과와 실제 결과를 적어 주세요. 스크린샷·로그의 개인정보는 지우고, 세이브와 추출한 게임 자료는 올리지 마세요.

## 권리·책임·개인정보 안내

비공식 도구이며 있는 그대로 제공합니다. 게임과 제3자 자료의 권리는 각 권리자에게 있습니다. 법률상 배제할 수 없는 권리는 제한하지 않습니다.

[Full notice / English · 한국어](../../DISCLAIMER.md) · [Privacy / English · 한국어](../../PRIVACY.md)

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
