# Corresponding sources for bundled libraries

Each binary release is intended to include a separate asset named
`BallxPitCompanion-<version>-dependency-sources.zip`, available next to the application ZIP
at no additional charge. **Publish both assets together.** A local build is not a published offer.

The source bundle contains the unmodified source archives for:

- Qt Base: the bundled Qt Core, GUI, Network, and Widgets libraries;
- Qt SVG: the bundled SVG library and related plugin;
- Qt for Python: PySide6 and Shiboken;
- pynput.

The exact versions, official download URLs, and SHA-256 digests are recorded in
`vendor/licenses/corresponding-sources.json` in the repository and in `corresponding-sources.json`
inside the source bundle. Upstream build files and source notices remain inside those archives.
We do not maintain local patches to these libraries. The application build recipe is
`BallxPitCompanion.spec`, `build_exe.ps1`, and the pinned `requirements*.txt` files in the same
application source revision. Python dependency versions are also recorded in the application's
`_internal/third_party/dependency-notices.json`.

The application uses replaceable shared libraries; pynput remains replaceable Python source.
You may modify the libraries, substitute compatible builds, and debug the resulting application.
No project notice is intended to restrict rights granted by their licenses. License texts are
included in the application bundle. The application's original source remains under MIT.

## Maintainer procedure

```powershell
.venv\Scripts\python.exe tools\package_sources.py --download --bundle-dir dist\BallxPitCompanion
```

The tool checks the installed package versions, actual bundled Qt modules, and source hashes.
After upgrading dependencies, review and update the manifest using their official releases.
Do not publish an old source archive alongside newer libraries. Keep the source asset available
for as long as the corresponding binary remains available, including older releases.

If the source asset is missing or inaccessible, report the release tag through the repository's
issue tracker. Maintainers must restore access or withdraw the affected binary download until
the corresponding source is available. This procedure supports license compliance; it is not
a legal certification of every jurisdiction or downstream distribution.

## 한국어 요약

실행 파일을 공개할 때 같은 릴리스에 `dependency-sources.zip`도 함께 올립니다. Qt Base·Qt SVG·
PySide/Shiboken·pynput의 수정하지 않은 대응 버전 소스와 해시·출처를 담습니다. 라이선스 원문만
넣고 소스 제공을 생략하지 않습니다. 의존성 버전을 바꾸면 소스 목록도 다시 확인하고, 이전 바이너리를
배포하는 동안에는 해당 소스도 계속 내려받을 수 있게 유지합니다. 사용자의 라이브러리 수정·교체·
디버깅 권리는 제한하지 않습니다. 이 파일은 유지관리 절차이며 법률 검토를 대신하지 않습니다.
