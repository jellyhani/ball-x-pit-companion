# Privacy and local data

[한국어](#한국어) · [Project notice](DISCLAIMER.md)

This describes the project's code, not the behavior of independently installed mods or services.

## What the companion reads

- Game state supplied by the local bridge: choices, loadouts, resources, base geometry, unlock
  records, and other values needed for recommendations.
- Game installation files for locally extracted labels and icons, and game logs for fallback
  tracking. The bridge does not require your Steam password or an account token.
- The game window region for local Windows OCR/icon matching when screen recognition is used.
  Visible content covering that region may also appear in a diagnostic capture.
- Supported global hotkeys and recent mouse-click positions/times. Ordinary typed text is not
  recorded as a keystroke log. The input watcher does not send gameplay input.

## What stays on this PC

Settings, extracted assets, cached catalogs, run/choice history, diagnostic logs, base snapshots,
and harvest traces are stored locally, normally under `%LOCALAPPDATA%\BallxPitCompanion`.
Development setups can override their output directory. Manually saved diagnostic images can
contain visible game or desktop content; logs can contain local usernames and file paths.

The companion has no built-in account system, advertising tracker, or automatic gameplay-log
upload. Its bridge communicates over a local named pipe. Local storage is not encrypted by this
application; other software or users with access to those files may be able to read them.

`logs/diagnostics.jsonl` records processing stages, state changes, calculation request fingerprints,
queue and execution times, and reasons a result was accepted, discarded, or not displayed.
It uses selected metadata rather than copying raw game snapshots or calculation inputs. Repeated
unchanged states are summarized. This log rotates at 5 MB with four backups (about 25 MB total);
that limit does not apply to the separate snapshots, history files, or ordinary application log.

## Network access and sharing

Setup and bridge installation download dependencies or BepInEx from their distribution services.
Those services receive ordinary connection data, such as the requesting IP address, under their
own policies. Opening documentation or reporting an issue also contacts an external service.
The claim that gameplay records stay local does not mean installation works entirely offline.

Optional developer tools for an AI-assisted improvement loop are separate from the companion.
If a maintainer explicitly enables them, they can send a task to their configured coding service;
that service's data handling and account terms apply. They are not enabled by installing the companion.

Issue reports and attached logs/screenshots are shared only when you choose to submit them.
Inspect and redact them first. Do not publish saves, credentials, private conversations, or
extracted game assets. GitHub issues may become publicly accessible when the repository is public.

## Removal

Close the companion before removing its local data folder. This removes companion settings and
records, not the game's save. Uninstalling the bridge is a separate action; shared BepInEx files
are retained so other installed mods are not removed. Back up anything you want to keep first.

---

## 한국어

이 문서는 본 프로젝트의 동작을 설명합니다. 별도로 설치한 모드·외부 서비스의 동작까지 설명하지 않습니다.

- 추천을 위해 게임 선택지·보유 항목·자원·기지 모양·해금 기록 등을 로컬 연동으로 읽습니다.
- 설치된 게임에서 표시용 문구·아이콘을 추출하고, 보조 추적에 게임 로그를 사용합니다.
  Steam 비밀번호나 계정 토큰을 요구하지 않습니다.
- 화면 인식을 사용할 때 게임 창 영역을 로컬 OCR·아이콘 비교에 사용합니다. 그 영역을 가린 다른
  창의 내용이 진단 이미지에 포함될 수 있습니다.
- 지정 단축키와 최근 마우스 클릭 위치·시각을 관측합니다. 일반 입력 문장을 키 입력 기록으로
  저장하지 않으며, 게임 입력을 대신 보내지 않습니다.
- 설정·추출 자료·카탈로그·런/선택 기록·진단 로그·기지 스냅샷·채집 궤적은 보통
  `%LOCALAPPDATA%\BallxPitCompanion`에 저장됩니다. 개발 설정에서는 경로를 바꿀 수 있습니다.
  진단 자료에는 로컬 사용자명·경로 또는 화면에 보인 내용이 포함될 수 있습니다.
- 자체 계정·광고 추적·플레이 기록 자동 업로드 기능은 없습니다. 연동은 로컬 named pipe를 사용합니다.
  저장 파일을 앱 자체에서 암호화하지 않으므로, 접근 권한을 가진 다른 사용자나 프로그램이 읽을 수 있습니다.
- `logs/diagnostics.jsonl`에는 처리 단계·상태 변화·계산 요청 지문·대기 및 실행 시간·결과 적용/폐기/표시
  사유를 기록합니다. 원본 스냅샷이나 계산 입력 전체 대신 선택한 메타데이터를 남기며, 같은 상태는 요약합니다.
  파일당 5MB, 이전 파일 4개로 총 약 25MB를 유지합니다. 별도 스냅샷·이력·일반 로그에는 이 제한이 적용되지 않습니다.
- 초기 설정·연동 설치에는 의존성과 BepInEx 다운로드가 있습니다. 다운로드 서비스는 IP 주소 등
  일반적인 접속 정보를 자체 정책에 따라 처리합니다. ‘플레이 기록을 업로드하지 않는다’는 설명은
  설치 과정에도 인터넷 접속이 전혀 없다는 뜻이 아닙니다.
- 개발자용 AI 개선 루프는 도우미와 별도입니다. 유지관리자가 직접 켜면 설정한 코딩 서비스에 작업을
  전달할 수 있으며 해당 서비스의 정보 처리·계정 약관이 적용됩니다. 도우미 설치로 자동 활성화하지 않습니다.
- 이슈에 자료를 직접 올리기 전에 개인정보를 지워 주세요. 세이브·인증 정보·비공개 대화·추출한
  게임 자료는 공개하지 마세요. 공개 저장소의 이슈와 첨부 파일은 다른 사람이 볼 수 있습니다.
- 도우미 종료 후 로컬 자료 폴더를 지우면 도우미 설정·기록이 삭제됩니다. 게임 세이브 삭제와는 다릅니다.
  연동 플러그인 제거는 별도 기능이며, 다른 모드 보존을 위해 공유 BepInEx 런타임은 남깁니다.
