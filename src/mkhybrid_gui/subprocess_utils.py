"""外部コマンドをmacOS上で安全に起動するための共通ヘルパー。

**背景（実機・実行環境で確認した重大な問題）**: 本アプリはPySide6（Qt）
を使うGUIアプリであり、必然的にマルチスレッドになる（Qt自体の内部
スレッド、各種`QThread`ワーカー）。CPythonの`subprocess`モジュールは、
このアプリのようにコマンド名だけ（PATH検索に頼る形）を渡し、
`close_fds`を既定値（`True`）のまま呼び出す場合、macOS/Linux問わず
常に`fork()` + `exec()`を使う。`posix_spawn()`という、より安全な
高速パスが使われるのは、**実行ファイルを絶対パスで渡し、かつ
`close_fds=False`を指定した場合のみ**（CPython 3.11の
`subprocess._use_posix_spawn`/`Popen._execute_child`の条件分岐で
確認済み）。

マルチスレッドプロセスで`fork()`するのは一般に危険とされるが、
実際にこのプロジェクトのテストスイート実行中、`afconvert`
（macOS標準コマンド）が`SIGSEGV`で頻繁にクラッシュする問題が発生し、
macOSのクラッシュレポートを解析した結果、原因は
`fork()` → `_pthread_atfork_child_handlers` →
（Qtの`QtDBus`/`QtNetwork`をロードしたことで登録される）Apple
Network.frameworkの子側atforkハンドラ内でのクラッシュであることを
特定した。実行ファイルを絶対パスで渡し`close_fds=False`にすることで
`posix_spawn()`の高速パスが使われるようになり、再現しなくなることを
確認済み。

このアプリはGUI本体（PySide6）から外部コマンド
（`hdiutil`/`diskutil`/`cd-paranoia`/`afconvert`/`flac`/`cdrdao`/
`toc2cue`）を全てQThreadワーカーから呼び出すため、テストだけでなく
**本番のGUIアプリ自体でも同じクラッシュが起こりうる**。全ての
`subprocess.run`/`subprocess.Popen`呼び出しは、必ずこのモジュールの
`resolve_command`を経由してコマンド名を絶対パスに解決し、
`close_fds=False`を指定すること。

`terminate_with_escalation`は別の関心事（中断処理の安全性）を扱う。
`IsoWorker`/`AudioRipWorker`/`CdrdaoWorker`の`request_cancel()`は
実行中プロセスへ`terminate()`（SIGTERM）を送るが、コマンドが
シグナルを無視・処理に時間がかかる場合、出力読み取りループが
ハングし続けGUIが閉じられなくなる。一定時間後に`kill()`
（SIGKILL）へ自動的にエスカレーションすることで、この既知の制限
（README.mdの「ロードマップ・既知の制限」を参照）を解消する。
"""

from __future__ import annotations

import shutil
import subprocess
import threading

#: `posix_spawn()`の安全な高速パスを使うために、すべての外部コマンド
#: 実行で指定すること。このモジュールのdocstringを参照。
SAFE_SUBPROCESS_KWARGS: dict[str, bool] = {"close_fds": False}

#: `terminate_with_escalation()`の既定のエスカレーション猶予秒数。
DEFAULT_TERMINATE_ESCALATION_SECONDS = 5.0


def resolve_executable(name: str, path: str | None = None) -> str:
    """コマンド名を絶対パスに解決する。

    `subprocess`がクラッシュ安全な`posix_spawn()`の高速パスを使うには、
    実行ファイルを絶対パスで渡す必要がある（コマンド名だけだとPATH検索の
    ため`fork()`+`exec()`にフォールバックし、マルチスレッドのQtプロセス
    ではクラッシュしうる。詳細はこのモジュールのdocstringを参照）。

    解決できない場合はコマンド名をそのまま返す（呼び出し元は通常
    事前に`missing_tools()`等で有無を確認済みの前提だが、万一解決
    できなくても、従来どおりPATH検索によるフォールバック実行を試みる
    ため、処理自体は継続できる）。
    """
    resolved = shutil.which(name, path=path)
    return resolved if resolved is not None else name


def resolve_command(cmd: list[str], path: str | None = None) -> list[str]:
    """コマンドリストの先頭（実行ファイル名）だけを絶対パスに解決した
    新しいリストを返す（残りの引数はそのまま）。
    """
    if not cmd:
        return cmd

    return [resolve_executable(cmd[0], path=path), *cmd[1:]]


def terminate_with_escalation(
    process: subprocess.Popen[str],
    timeout: float = DEFAULT_TERMINATE_ESCALATION_SECONDS,
) -> None:
    """プロセスへ``SIGTERM``を送り、``timeout``秒経っても終了しない
    場合は``SIGKILL``へエスカレーションする。

    ``SIGTERM``を無視する（あるいは終了処理に時間がかかる）外部
    コマンドが相手だと、``terminate()``を送るだけではワーカー
    スレッドが出力読み取りループでハングし続け、GUIが
    ``closeEvent``のガードにより永久に閉じられなくなる（README.md
    の「ロードマップ・既知の制限」を参照）。エスカレーションの監視は
    バックグラウンドの``threading.Timer``で行うため、呼び出し元
    （通常はGUIスレッド）をブロックしない。
    """
    process.terminate()

    def _escalate() -> None:
        if process.poll() is None:
            process.kill()

    timer = threading.Timer(timeout, _escalate)
    timer.daemon = True
    timer.start()
