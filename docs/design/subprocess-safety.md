# 外部コマンド呼び出しのクラッシュ安全性（`subprocess_utils.py`）

対象: 実装全体（ISO作成・音楽CD双方が利用する全レイヤー）。

## 1. 背景・発見の経緯

`make test`実行時、`tests/test_audio_cd.py`の`test_write_metadata_tags_mp4`
（テストヘルパー`_build_minimal_m4a`内で`afconvert`を呼び出す）が、
毎回ではないが高い再現性で`SIGSEGV`によりPythonプロセスごとクラッシュする
現象が発生した。単なるテストのflakinessではなく、macOSのクラッシュ
ダイアログ（「Pythonが予期しない理由で終了しました」）が表示される、
実際のプロセスクラッシュだった。

`~/Library/Logs/DiagnosticReports/Python-*.ips`のクラッシュレポートを
解析したところ、以下のバックトレースを確認した。

```
fork
→ _pthread_atfork_child_handlers
→ nw_settings_child_has_forked()
→ nw_path_release_globals
→ NEFlowDirectorDestroy
→ (SIGSEGV)
```

`"asi"`セクションには`"CoreFoundation": ["*** multi-threaded process forked ***"]`
という診断メッセージが含まれていた。

## 2. 根本原因

### 2.1 CPythonの`subprocess`が`fork()`にフォールバックする条件

CPython（macOS）の`subprocess.Popen._execute_child()`は、以下の条件を
**すべて**満たす場合のみ、クラッシュ安全な`posix_spawn()`の高速パスを使う
（`subprocess._USE_POSIX_SPAWN`はmacOSでは`True`）。

```python
if (_USE_POSIX_SPAWN
        and os.path.dirname(executable)   # 絶対/相対パスが必要。バレ名は不可
        and preexec_fn is None
        and not close_fds                  # close_fds=False が必要
        and not pass_fds
        and cwd is None
        and (p2cread == -1 or p2cread > 2)
        and (c2pwrite == -1 or c2pwrite > 2)
        and (errwrite == -1 or errwrite > 2)
        and not start_new_session
        and gid is None and gids is None and uid is None
        and umask < 0):
    self._posix_spawn(...)
    return
```

このリポジトリの既存コードは、外部コマンドを一貫して**バレ名**
（例: `["afconvert", ...]`）で呼び出し、`close_fds`は指定していなかった
（Pythonの既定値は`True`）。そのため上記条件を満たせず、**すべての
`subprocess.run`/`Popen`呼び出しが`fork()+exec()`にフォールバックしていた**。

### 2.2 なぜ`fork()`がこのアプリでクラッシュしうるか

本アプリはPySide6（Qt）のGUIアプリであり、`QThread`によるマルチスレッド
実行が前提（AGENTS.mdの「非同期処理」を参照）。加えてQtは内部で
`QtDBus`/`QtNetwork`をロードしており、これらはApple Network.framework
の`pthread_atfork`子プロセス側ハンドラ（`nw_settings_child_has_forked`等）
を登録する。

マルチスレッドプロセスを`fork()`した場合、子プロセス側には**呼び出し
スレッド1本だけ**しか複製されない（他スレッドが保持していたロック等は
そのままの状態で複製される）ため、`fork()`直後・`exec()`前に実行される
`pthread_atfork`ハンドラがロック待ちやNULL参照でクラッシュしうる。
これは一般に知られたUNIXの「マルチスレッドプロセスのfork」問題であり、
今回のクラッシュレポートはその実例そのものだった。

## 3. 検証した修正方法

実行ファイルを`shutil.which`で**絶対パスに解決**し、かつ`close_fds=False`
を指定することで、`posix_spawn()`の高速パスの条件を満たせることを
実機で確認した（`_build_minimal_m4a`ヘルパーに対する一時的なパッチで
仮説検証: `close_fds=False`のみでは再現し続けたが、絶対パス解決と
併用したところクラッシュが解消した）。

`posix_spawn()`は`fork()`を経由しないため（Linuxの`vfork`同様、あるいは
`posix_spawn(2)`システムコール経由）、マルチスレッドプロセスからの
呼び出しでも上記の`pthread_atfork`クラッシュを回避できる。

## 4. 実装（`subprocess_utils.py`）

```python
SAFE_SUBPROCESS_KWARGS: dict[str, bool] = {"close_fds": False}

def resolve_executable(name: str, path: str | None = None) -> str:
    resolved = shutil.which(name, path=path)
    return resolved if resolved is not None else name

def resolve_command(cmd: list[str], path: str | None = None) -> list[str]:
    if not cmd:
        return cmd
    return [resolve_executable(cmd[0], path=path), *cmd[1:]]
```

- `resolve_command()`は先頭要素のみを絶対パスへ解決する（引数はそのまま）。
- 解決に失敗した場合（`shutil.which`が`None`を返す）は元のバレ名を
  そのまま返す。実行時に「コマンドが見つからない」エラーとして通常どおり
  表面化させるため（`missing_tools()`系のチェックを迂回させないため）。
- `iso_builder.py`/`audio_cd.py`/`disk_utils.py`/`cdrdao.py`の全ての
  `subprocess.run`/`Popen`呼び出しがこれを経由する。Homebrew経由のコマンド
  （`cd-paranoia`/`flac`/`cdrdao`/`toc2cue`）は`audio_cd.effective_path()`
  （ログインシェルのPATHを含む）を`resolve_command()`の`path`引数に渡す。
  macOS標準コマンド（`diskutil`/`hdiutil`/`afconvert`）はプロセス既定の
  `PATH`（`/usr/bin`等、常に含まれる）で解決できるため`path`省略で足りる。

## 5. テストへの影響

このマシンには実際に`hdiutil`/`cd-paranoia`/`cdrdao`等がインストール
されているため、`shutil.which`は本物の絶対パス（例:
`/opt/homebrew/bin/cdrdao`）を返す。そのため、Popenモックの引数を
バレ名の完全一致（`cmd[:2] == ["diskutil", "info"]`等）で検証していた
既存テストの多くが影響を受けた。各テストファイルに以下の正規化ヘルパーを
追加し、比較の直前に適用することで対処した（`build_*_command()`のような
コマンド組み立てだけを検証する純粋関数テストは、解決前のバレ名のままで
問題なく、変更していない）。

```python
def _norm(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    return [Path(cmd[0]).name, *cmd[1:]]
```

## 6. 関連するAGENTS.mdの記載

- 「技術スタック」の`subprocess`呼び出しのクラッシュ安全性の項。
- 「やってはいけないこと」の該当項目（`resolve_command`/
  `SAFE_SUBPROCESS_KWARGS`を省略しない、テストのアサーションも
  絶対パス解決後を前提に書く）。
