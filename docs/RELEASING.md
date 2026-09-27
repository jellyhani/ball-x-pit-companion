# 공개 배포 절차

## 저장소 공개와 바이너리 배포

공개 저장소는 현재 파일뿐 아니라 Git 이력도 공개합니다. 세이브·추출 게임 자료·개인 경로·인증 정보·
로컬 대화/인수인계가 추적되지 않는지 확인합니다. `.gitignore` 추가만으로 이미 커밋한 이력이 사라지지는 않습니다.
다른 작업의 로컬 변경을 릴리스 커밋에 섞지 않습니다.

`LICENSE`는 프로젝트의 원본 소프트웨어, `NOTICE.md`는 제3자 구성요소,
`DISCLAIMER.md`는 비공식 관계·무보증·책임 범위, `PRIVACY.md`는 로컬 기록·외부 다운로드를 설명합니다.
이 문서들은 제3자의 허락이나 개별 법률 검토를 대신하지 않습니다.

## 로컬 확인

1. 앱 버전(`src/version.py`)과 변경 기록을 갱신합니다. 플러그인을 수정했다면 세 버전과 vendor DLL도 맞춥니다.
2. 전체 테스트, Python 대체 전체 테스트, 추천 회귀를 확인합니다. 건너뛴 검사와 실제 게임 미확인 사항을 기록합니다.
3. 실행 중인 도우미를 정상 종료하고 빌드합니다. 게임은 입력하거나 강제로 종료하지 않습니다.
4. `build_exe.ps1`은 테스트·패키징·합성 파이프 검사·의존성 소스 준비까지 성공해야 ZIP을 만듭니다.
5. 실행 파일 ZIP과 `dependency-sources.zip`을 함께 검토합니다. 후자는 배포한 Qt/PySide/Shiboken/pynput의 대응 소스입니다.
6. 바이너리의 `_internal/build-info.json`에 소스 revision이 있고 `dirty`가 false인지 확인합니다.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
$env:BXP_NO_NATIVE = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -t .
Remove-Item Env:BXP_NO_NATIVE
.venv\Scripts\python.exe tools\regress.py
dist\BallxPitCompanion\BallxPitCompanion.exe --stop
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

초기 소스 아카이브 다운로드에는 네트워크가 필요합니다. 버전이나 해시가 다르면 포장을 중단합니다.
대응 소스 제공 절차는 [DEPENDENCY_SOURCES.md](DEPENDENCY_SOURCES.md)를 참고하세요.

## 릴리스 본문에 포함할 내용

- 도우미·플러그인 버전, 지원 OS와 확인한 게임 버전.
- 변경 사항과 기본/대체 계산 검사, 실제 게임 확인 범위.
- 알려진 제한과 미검증 항목. beta 상태라면 명확하게 표시합니다.
- 실행 파일 ZIP, 대응 의존성 소스 ZIP, 각 SHA-256.
- 저장소의 `DISCLAIMER.md`, `PRIVACY.md`, `NOTICE.md` 링크.
- 전체 ZIP을 풀어 실행한다는 설치 설명과 플러그인 업데이트 시 게임을 정상 종료해야 한다는 안내.

소스 제공 자산이 빠진 상태로 바이너리만 공개하지 않습니다. 공개가 완료되면 실제 릴리스 페이지에서
로그인하지 않은 사용자도 파일을 내려받을 수 있는지 확인합니다. 이전 버전 다운로드를 유지한다면
그 버전의 대응 소스도 함께 유지합니다.
