# AGENTS.md

このリポジトリは、macOS上で光学ディスク（データCD/音楽CD/DVD/Blu-ray）からWindows/Linuxでも読めるISOイメージ（ISO 9660 + Joliet + Rock Ridge + 任意でUDF）を作成するための、Pythonベース GUIアプリケーションです。AIコーディングエージェントがこのプロジェクトを扱う際は、以下の方針に従ってください。

複雑な機能（アルゴリズムの設計判断・実機検証の経緯・採用しなかった代替案とその理由等）については、この`AGENTS.md`とは別に`docs/design/`配下に個別の設計ドキュメントを置く（例: [docs/design/accuraterip.md](docs/design/accuraterip.md)）。該当機能に変更を加えた場合は、実装だけでなく対応する設計ドキュメントも同じ変更の中で更新し、ドキュメントが実装より古いまま放置されないようにすること。

## プロジェクト概要

- **パッケージ名**: `mkhybrid-gui`（PyPI配布名・リポジトリ名）。Python内の `import` 名はハイフンを使えないため、モジュール名は `mkhybrid_gui`（アンダースコア表記）とする。
- **目的**: macOSに接続した光学ドライブの内容を、`hdiutil makehybrid` を用いて Windows/Linux 双方で読み取り可能なハイブリッドISOイメージに変換するGUIツールを提供する。
- **対応メディア**: データCD / 音楽CD（Audio CD） / DVD / Blu-ray（BD）。BDXL（大容量BD）・M-DISC（アーカイブ用メディア）は、OS上は通常のBD-R/BD-REと同じファイルシステムでマウントされるため、追加のメディア種別分岐は不要（[disk_utils.py](src/mkhybrid_gui/disk_utils.py) の `detect_media_type` はサイズベースの判定でこれらを自然にカバーする）。
  - データCD/DVD/BD: `hdiutil makehybrid` でISOイメージ化（ISO9660 + Joliet + Rock Ridge、DVD/BDでは大容量ファイル対応のUDFを追加可能）。
  - 音楽CD: [audio_cd.py](src/mkhybrid_gui/audio_cd.py) が `cd-paranoia`（誤り訂正・再読込付きの正確なリッピング）でWAVを取得し、`afconvert`（ALAC/AIFF/AAC）または `flac`（FLAC）でユーザー選択の形式に変換する。WAVはそのまま採用する。macOS標準のCDDAFSマウント（単純なAIFFコピー）は誤り訂正・検証ができないため使用しない。アルバム名・アーティスト名・トラック名は手動入力、または[musicbrainz.py](src/mkhybrid_gui/musicbrainz.py)経由のMusicBrainzオンライン検索（オプトイン）で取得し、[mutagen](https://mutagen.readthedocs.io/)でファイルにタグ付けする。
  - 音楽CD（追加オプション）: [cdrdao.py](src/mkhybrid_gui/cdrdao.py) が `cdrdao read-cd` でディスク全体をTOC+BINイメージとしてバックアップする。上記の正確なリッピング（トラックごとの変換・タグ付け）を置き換えるものではなく、GUI上でユーザーが選べる追加の書き出し方法。データ+音声混在（mixed-mode）ディスクやコピーガード付き等、既存の経路が使えない特殊なディスクのフォールバックとしても使う。トラックごとの変換・タグ付けは行わない。
- **対象OS**: macOS専用（`hdiutil` / `diskutil` はmacOS標準コマンドに依存するため、Windows/Linuxでは動作しない）。
- **追加の外部依存（Homebrew）**: `cd-paranoia`（音楽CDの正確なリッピングに必須。Homebrewパッケージ名は `libcdio-paranoia`）、`flac`（FLAC書き出し時のみ必須。パッケージ名も `flac`）、`cdrdao`（ディスクイメージ作成オプションを使う場合のみ必須。パッケージ名も `cdrdao`、homebrew-coreでbottled提供されていることを確認済み）。これらはmacOS標準コマンドではないため、利用者に `brew install libcdio-paranoia flac cdrdao` の実行を求める。ISOイメージ作成（データCD/DVD/BD）はこれらに依存しない。
  - コマンド名とHomebrewパッケージ名が一致しない（`cd-paranoia` ⇔ `libcdio-paranoia`）ため、パッケージ名を書く箇所では取り違えないこと。対応表は [main_window.py](src/mkhybrid_gui/ui/main_window.py) の `_BREW_PACKAGES` を参照。
- **追加の外部依存（PyPI）**: [`mutagen`](https://mutagen.readthedocs.io/)（音楽ファイルへのタグ書き込みに必須。`pyproject.toml` の `dependencies` に含まれ、Homebrewではなく `pip install -e ".[dev]"` で導入される）。純Python実装で外部バイナリに依存しない。ネットワーク通信自体は標準ライブラリの `urllib.request` のみを使い、`musicbrainz.py` のために追加パッケージを導入しない（新たな依存を増やす前に、まず標準ライブラリで足りないか検討すること）。
- **外部コマンドの解決（PATH）**: GUIアプリとして（Finder等から）起動された場合でも、macOS/launchdはプロセスに `/usr/bin:/bin:/usr/sbin:/sbin` 程度の最小限の`PATH`を設定するため、`PATH`が非空であることは「必要なコマンドが見つかる」ことを意味しない。[audio_cd.py](src/mkhybrid_gui/audio_cd.py) の `effective_path()` は、`PATH`の空/非空にかかわらず**常に**ログインシェルの`PATH`（Homebrewのインストール先を含む）を取得してプロセスの`PATH`とマージする。「`PATH`が空の場合だけログインシェルを問い合わせる」という条件分岐に戻すと、GUI起動時にHomebrewのコマンドが見つからなくなる回帰バグになるため、変更する際は必ずこの前提を守ること。この関数は先頭アンダースコアを付けない公開関数であり（`tool_path()`/`command_env()`も同様）、Homebrewインストールのコマンドに依存する他のモジュール（[cdrdao.py](src/mkhybrid_gui/cdrdao.py)）からも再利用する。同じPATH解決ロジックを他モジュールで再実装しないこと（過去に回帰した実績のある箇所を2箇所に増やさないため）。
- **想定ユーザー**: 社内配布用メディアの作成を行う非エンジニアも含む担当者。CLIを意識させず、GUIから完結させる。

## 技術スタック

- **言語**: Python 3.11以降
- **GUIフレームワーク**: `PySide6`（Qt for Python）。LGPLv3ライセンスのため、README等にライセンス表記を含めること。
- **非同期処理**: `QThread`（または `QRunnable`/`QThreadPool`）+ シグナル/スロット方式で `subprocess.Popen` の出力を非同期にUIへ反映する。

  ```python
  class IsoWorker(QThread):
      progress = Signal(str)
      finished_ok = Signal(bool)

      def run(self):
          proc = subprocess.Popen([...], stdout=subprocess.PIPE, text=True)
          for line in proc.stdout:
              self.progress.emit(line)
          self.finished_ok.emit(proc.wait() == 0)
  ```

- **外部コマンド呼び出し**: `subprocess`（macOS標準: `diskutil list`, `diskutil info`, `hdiutil makehybrid`, `hdiutil verify`、`afconvert`。Homebrew依存: `cd-paranoia`, `flac`）。使用前に `shutil.which` で有無を確認し、不足時はインストール方法（`brew install ...`）をGUIに明示する（`audio_cd.missing_tools`）。
- **`hdiutil makehybrid` の制約（実機で確認済み）**: `-rock`・`-puppetstrings` オプションは受け付けない（指定すると `-puppetstrings option not allowed` 等で失敗する）。Rock Ridge拡張は `-iso` を指定した時点で自動的に有効になるため、`-rock` を明示的に渡す必要はない。また `-verbose` を付けても含めても、ビルド中に機械可読な進捗率（パーセント）は一切出力されない（`hdiutil verify` も同様）。そのため [iso_builder.py](src/mkhybrid_gui/iso_builder.py) の `on_percent` コールバックは、ISO作成フェーズでは実質的に呼ばれない前提でGUI側を設計すること（進捗バーはビジー表示にフォールバックする）。これらの制約を「バグ」と誤認して `-rock`/`-puppetstrings` をコマンドに追加する修正をしないこと。
- **パッケージング**: `PyInstaller` を用いて `.app` バンドルを生成する（Qtプラグインの取りこぼしを避けるため `py2app` ではなくこちらを採用。配布時はコード署名なしのadhoc署名で可）。`plugins/platforms`（`libqcocoa.dylib`等）が正しく同梱されるかをビルド手順で確認する。
- **UI構成**: レイアウトは `QVBoxLayout`/`QHBoxLayout`/`QFormLayout` 等で構成する。必要に応じてQt Designerの`.ui`ファイル＋`pyside6-uic`変換によるコード分離運用も選択可とする。

## ディレクトリ構成（想定）

```
.
├── AGENTS.md
├── README.md
├── pyproject.toml          # name = "mkhybrid-gui"
├── Makefile                 # build/test/install/clean/distclean
├── src/
│   └── mkhybrid_gui/
│       ├── app.py              # エントリポイント / GUI起動
│       ├── config.py           # 設定値の集約・config.tomlの読み書き（AppConfig/UiPreferences）
│       ├── disk_utils.py       # diskutil list/info のパース、メディア種別（MediaType）判定
│       ├── iso_builder.py      # hdiutil makehybrid / 検証(attach+verifyVolume) のラッパー、IsoWorker(QThread)
│       ├── audio_cd.py         # cd-paranoiaによる正確なリッピング、afconvert/flacでの形式変換、TOC解析、mutagenタグ付け、AudioRipWorker(QThread)
│       ├── metadata.py         # AlbumMetadata/TrackMetadata、MusicBrainz Disc ID計算（純ロジック）
│       ├── musicbrainz.py      # MusicBrainz Web Serviceへの問い合わせ（urllib標準ライブラリのみ）、MetadataLookupWorker(QThread)
│       └── ui/
│           └── main_window.py  # PySide6ウィジェット定義（「作成」タブ+「設定」タブのQTabWidget構成、メディア種別に応じてUIを切替、音楽CDメタデータ入力欄）
├── tests/
│   ├── conftest.py          # 全テスト共通フィクスチャ（実環境の設定ファイルからの隔離等）
│   ├── test_config.py
│   ├── test_disk_utils.py
│   ├── test_iso_builder.py
│   ├── test_audio_cd.py
│   ├── test_metadata.py
│   └── test_musicbrainz.py
└── mkhybrid_gui.spec        # PyInstaller用
```

## セットアップ・実行コマンド

```bash
# 音楽CDの正確なリッピング・FLAC書き出しに必要（データCD/DVD/BDのISO作成には不要）
brew install libcdio-paranoia flac

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"   # PySide6・pytest等をpyproject.tomlの[project.optional-dependencies]から導入
python -m mkhybrid_gui.app
```

依存関係は `requirements.txt` ではなく `pyproject.toml` の `[project]`/
`[project.optional-dependencies]` で管理する。

## ビルド（.app化）

```bash
pip install pyinstaller
pyinstaller mkhybrid_gui.spec
```

## Makefile

`.venv` のセットアップを含め、以下のターゲットで一通りの操作ができる。

```bash
make build      # .venv を用意し、PyInstallerで .app をビルド
make test       # .venv を用意し、pytest を実行
make install    # build を実行し、.app を $(PREFIX)（既定: /Applications）へインストール
make clean      # build/dist/キャッシュ等の生成物を削除（.venvは残す）
make distclean  # clean に加えて .venv も削除
```

## コーディング規約

- 型ヒントを必須とする（`subprocess.run` の戻り値、パス引数など）。
- `subprocess` 呼び出しは `shell=True` を使わず、引数はリストで渡す（パスにスペースや日本語が含まれるケースに対応するため）。
- macOS依存コマンド（`hdiutil`, `diskutil`）の実行結果は必ずreturncodeとstderrをチェックし、GUI側にエラーメッセージとして表示する。
- ハードコードされた `/dev/diskN` を避け、`diskutil list -plist` の出力をパースして選択肢をユーザーに提示する。
- ビジネスロジック層（`disk_utils.py`, `iso_builder.py`, `audio_cd.py`, `metadata.py`, `musicbrainz.py`, `config.py`）はUIフレームワーク（PySide6）に依存しない設計とし、単体でテスト可能にする。`musicbrainz.py` はネットワークI/O部分（`url_opener`）を差し替え可能にし、実ネットワークを使わずにテストできるようにする。

## 主要な実装要件（機能仕様）

1. **デバイス/ドライブ選択**: `diskutil list` の結果からマウント済みボリュームの一覧をGUI上に表示し、ユーザーに選択させる。各ボリュームは `diskutil info -plist` の `FilesystemType` とサイズから `MediaType`（データCD/音楽CD/DVD/BD）を推定し、選択肢のラベルに表示する（`disk_utils.detect_media_type`）。`filesystem_type` が既知の非光学ファイルシステム（`apfs`等）と判明している場合は `MediaType` を付与せず、`disk_utils.list_volumes()` はそのようなボリューム（内蔵の起動ディスク等）をGUIの選択肢から除外すること。
2. **イメージ作成（データCD/DVD/BD）**: 選択対象に対して以下を実行する。
   ```
   hdiutil makehybrid -iso [-joliet] [-udf] -o <出力先.iso> <デバイスorマウントポイント>
   ```
   - GUI上の「Rock Ridge」チェックボックスはデフォルトONだが、`hdiutil makehybrid` 自体は `-rock` オプションを受け付けない（`-iso` 指定時にRock Ridge拡張が自動的に有効になるため）。したがって実際にコマンドへ渡すのは `-iso`/`-joliet`/`-udf` のみでよく、`-rock`を渡そうとする修正はしないこと（詳細は「技術スタック」の `hdiutil makehybrid` の制約を参照）。
   - `-joliet`・`-udf` はGUI上のチェックボックスで切り替え可能にする。
   - 「Joliet/UDFのいずれかを有効に」というGUI側のオプション検証には `rock` を含めない（上記の理由により、Rock Ridgeだけ有効な状態と全項目無効な状態は生成コマンドが同一になるため。実機で発見・修正済みの回帰、詳細は「やってはいけないこと」を参照）。
   - `-udf` はDVD/BD（BDXL・M-DISCを含む）選択時にデフォルトON（ISO9660の4GBファイルサイズ上限を回避するため）、CD選択時はデフォルトOFF。ユーザーは任意に変更できる。
3. **音楽CDの正確なリッピング**: `MediaType.CD_AUDIO` を選択した場合、ISO作成UIの代わりに出力先フォルダ選択・書き出し形式（ALAC/AIFF/FLAC/WAV/AAC、既定はALAC）・検証チェックボックスに切り替える。以下の3要件を満たすこと。
   1. **正確な読み取り**: `cd-paranoia` をパラノイアモード（`-Z` を指定しない）で実行し、ジッター補正・C2エラー利用を有効にする。
   2. **誤り訂正・再読込**: 上記はcd-paranoia自体が内部で行う（再実装しない）。
   3. **検証**: 1トラックを独立して複数回（既定2回、不一致なら最大3回まで）リッピングし、WAVのSHA-256チェックサムが一致することを確認する（`audio_cd.rip_track_verified`）。一致しなければ最後の読み取りを「未検証」として採用し、完了メッセージで警告する。
   - 取得したWAVは `afconvert`（ALAC/AIFF/AAC）または `flac`（FLAC）でユーザー選択の形式に変換する（`audio_cd.convert_audio`）。WAV選択時は変換不要でそのまま採用する。
   - 実行前に `audio_cd.missing_tools` で必要な外部コマンドの有無を確認し、不足時は `brew install ...` の案内を表示して処理を開始しない。
   - `cd-paranoia` の出力に含まれる `"Option not supported by drive"`（ドライブが一部の拡張コマンド・オプション機能に対応していないという診断メッセージで、リッピング自体には影響しない）は、ログにはそのまま表示しつつ、初回出現時にのみ「エラーではない」旨の補足を1行添えること（`MainWindow._on_progress`）。実機での報告により、説明なしではエラーだと誤解されやすいことが判明した。
4. **進捗表示**: `IsoWorker` / `AudioRipWorker`（いずれも `QThread`）で非同期実行し、`progress` シグナルでメインスレッドのUIを更新する。処理中はGUIをブロックしないこと。
   - `hdiutil makehybrid` は実際には進捗率を出力しないため、ISO作成フェーズの進捗バーは不確定（ビジー）表示のままにし、検証フェーズ開始・完了時のみ確定値（90%/100%）へ切り替える（実進捗率が得られたと偽装しない）。
   - 音楽CDのリッピングは、トラック単位（現在のトラック番号／総トラック数）の粗い進捗率を `progress_percent` シグナルで通知する。
5. **検証（ISO）**: ISOイメージ作成後、`iso_builder.verify_iso()` が以下の手順で検証する（**`hdiutil verify` は使用しない**）。検証中の出力も（完了を待たず）その場でGUIへストリーミング表示すること（`subprocess.run` で完了まで待ってからまとめて表示する実装に戻さない）。
   - **`hdiutil verify` を使わない理由（実機確認済み）**: `hdiutil makehybrid` が生成するISOイメージにはチェックサムが一切含まれない（`hdiutil imageinfo` で `Checksummed: false` / `Checksum Type: なし` を確認済み）。そのため `hdiutil verify` は、内容が正しい正常なISOイメージに対しても**必ず** `"has no checksum"` で失敗する。
   - 代わりに、`hdiutil attach -readonly` でイメージを実際にattachし、マウントされたファイルシステムが `udf` の場合のみ `diskutil verifyVolume`（内部的に `fsck_udf` を使用）でファイルシステムの整合性を検証する。意図的にtruncateした壊れたISOに対して `Bad extent in file` / `Filesystem is dirty` を正しく検出できることを実機で確認済み。
   - UDFを含まない（ISO9660/Jolietのみの）イメージについては、macOS側に対応するファイルシステム検証ツールが存在せず、`diskutil verifyVolume` は常に `"Invalid request (-69886)"` で失敗する（実機確認済み）。CD選択時はUDFが既定OFFのため、これが最も一般的なケースになる。
     - `source`（`IsoWorker`に渡されたISO作成元、通常は元ディスクのマウントポイント）が実在するディレクトリの場合、`iso_builder.compare_contents()` で、attach後のマウントポイントと元のディレクトリのファイル一覧・内容を比較する（`MainWindow`は必ず`source`を渡すこと。渡し忘れると検証が実質何もしないattach確認のみに後退する、実機で発見された回帰）。まずファイルサイズを比較し、サイズが一致するファイルについてのみSHA-256ハッシュも比較する（同一サイズのまま内容だけ壊れているケースも検出できることを実機で確認済み）。不一致があれば検証失敗として報告する。
     - `source` が渡されない、またはディレクトリとして存在しない場合にのみ、従来通りattachできたことのみをもって検証成功とみなす（後方互換のフォールバック）。
   - 検証後は成功・失敗によらず必ず `hdiutil detach` でデタッチする。
6. **エラーハンドリング**: コピーガード付きメディア等でセクタ単位読み取りが必要なケースを検出できない場合は、明確なエラーメッセージを表示し、対処法（別ツールの利用など）を案内する。
7. **安全な中断**: 実行中のISO作成・音楽CDリッピングは、GUI上の「中断」ボタンから中断できること。中断時は実行中の外部コマンドへ `terminate`（SIGTERM）を送り、作成途中の一時ファイル（WAV・部分的なISO等）を削除してから完了通知を出す（`IsoWorker.request_cancel` / `AudioRipWorker.request_cancel`、`audio_cd.RipCancelled`）。また、処理中はウィンドウを閉じられないようにし（`MainWindow.closeEvent`）、実行中のバックグラウンドスレッドを残したままアプリが終了しないようにすること。
8. **音楽CDのメタデータ**: 音楽CD選択時、アルバム名・アーティスト名・年・各トラック名を入力できるようにする（`metadata.AlbumMetadata`/`TrackMetadata`）。入力内容は以下に反映する。
   - **出力先ディレクトリ**: 指定した出力先フォルダ直下に複数回のリッピング結果が無秩序に混在しないよう、必ず何らかのサブディレクトリの中にトラックファイルを書き出す（`audio_cd.rip_and_convert_disc`の`fallback_folder_name`引数、実際の書き出し先は`RipResult.output_directory`で分かる）。サブディレクトリ名は、アルバム名が入力されていればそれ（`metadata.sanitize_filename_component` で安全な文字列に変換）を使い、未入力の場合は`fallback_folder_name`としてディスクのボリューム名（`Volume.volume_name`、無ければ`device_identifier`）を使う（`MainWindow._start_audio_rip()`）。アルバム名・ボリューム名のどちらも得られない場合に限り、従来通り出力先フォルダ直下に書き出す。`_start_audio_rip()`の「出力先が空でない」上書き確認も、この実際の書き出し先に対して行うこと。
   - **ファイル名**: トラックタイトルが入力されている場合は `NN - タイトル.拡張子`、未入力なら従来通り `TrackNN.拡張子`（`audio_cd.rip_and_convert_disc` 内、`metadata.sanitize_filename_component` でファイル名として安全な文字列に変換）。
   - **タグ**: `mutagen` を使い、形式ごとに適切なタグへ書き込む（`audio_cd.write_metadata_tags`）。MP4(ALAC/AAC)はiTunes系アトム、FLACはVorbis Comment、WAV/AIFFはID3v2。全項目未入力（アルバム名・アーティスト名・全トラックタイトルが空）ならタグ付けをスキップする。
   - **オンライン検索**: 「オンラインで検索（MusicBrainz）」ボタン（GUI上の明示的なクリックでのみ動作、自動実行しない）で、ディスクのTOCから計算した [MusicBrainz Disc ID](https://musicbrainz.org/doc/Disc_ID_Calculation)（`audio_cd.query_disc_toc` + `audio_cd.disc_id_from_disc_toc`）を使い `musicbrainz.lookup_releases` でMusicBrainzに問い合わせる。0件・複数件・ネットワークエラーのいずれも例外を投げず `LookupResult` として返し、リッピング処理自体を止めないこと。複数候補時はユーザーに選ばせ、既に入力がある場合は上書き前に確認する。
   - MusicBrainz API利用時は、意味のある `User-Agent` を送信し、1秒1リクエストのレート制限を守ること（`musicbrainz._wait_for_rate_limit`）。
9. **cdrdaoによるディスクイメージ（TOC+BIN）バックアップ**: 音楽CD選択時、既存の「正確なリッピング」（既定）に加え、「ディスクイメージ（cdrdao）」モードをラジオボタンで選べるようにする（`MainWindow.audio_mode_accurate_radio`/`audio_mode_cdrdao_radio`、`_on_audio_mode_changed()`）。既存のトラックごとの変換・タグ付け経路を置き換えるものではなく、データ+音声混在（mixed-mode）ディスクやコピーガード付き等の特殊なディスクのフォールバックとしても使う追加オプション。
   - cdrdaoモード選択時は、書き出し形式ラジオ・「厳密な検証」チェックボックス・アーティスト名/年入力欄・トラック名テーブル・MusicBrainz検索関連のウィジェットを隠す（トラック単位の変換・タグ付けを行わないため）。`album_edit`（アルバム名入力欄）は両モードで表示したままにする。
   - コマンドは`cdrdao read-cd --device <device> --driver generic-mmc-raw --paranoia-mode 3 --datafile <bin> <toc>`（`cdrdao.build_read_cd_command`）。`--paranoia-mode 3`は必ず指定すること（詳細は「やってはいけないこと」を参照）。
   - 出力ファイル名（`{base_name}.toc`/`.bin`）は、アルバム名入力欄（`album_edit`）の値を使い（`metadata.sanitize_filename_component`で安全な文字列に変換）、未入力の場合はディスクのボリューム名（`Volume.volume_name`、無ければ`device_identifier`）にフォールバックする（`MainWindow._start_cdrdao_rip()`）。既存の`audio_cd.rip_and_convert_disc`とは異なり、アルバム名サブフォルダは作らず、出力先フォルダ直下に直接書き出す（1回の実行につき1組のファイルのみのため、ISO作成と同じ扱い）。同名の`.toc`/`.bin`が既に存在する場合は上書き確認ダイアログを出す。
   - `cdrdao.missing_tools()`で`cdrdao`コマンドの有無を確認し、不足時は`brew install cdrdao`の案内を表示して処理を開始しない（PATH解決は`audio_cd.effective_path()`/`tool_path()`を再利用する。詳細は「技術スタック」の「外部コマンドの解決（PATH）」を参照）。
   - 選択したモード（`"ACCURATE"`/`"CDRDAO_IMAGE"`）は`UiPreferences.audio_rip_mode`として次回起動時にも復元する。
   - **デバイス指定（実機で確認済み）**: `cdrdao`の`--device`には、`cd-paranoia`/`hdiutil`が使う`/dev/rdiskN`は渡せない（`Cannot setup device`で失敗する）。代わりに`cdrdao scanbus`の出力に含まれるIOKitレジストリパス（例: `IOService:/AppleARMPE/.../IODVDServices`）が必要で、`diskutil info -plist`の`MediaName`（ドライブのモデル名）と`scanbus`出力の`vendor, model, rev`を突き合わせて特定する（`cdrdao.scan_bus()`/`cdrdao.find_scsi_device()`）。同一モデルのドライブが複数接続されている場合は区別できない（既知の制限）。
   - **排他アクセスのためのアンマウント（実機で確認済み）**: `cd-paranoia`と異なり、`cdrdao`はマウントされたボリュームがあると排他アクセスできず失敗する。音楽CDは各トラックが個別の`CD_DA`ボリュームとしてマウントされるため、単一パーティションの`diskutil unmount`では不十分で、ディスク全体を対象にする`diskutil unmountDisk`（`disk_utils.unmount_disk()`）が必要。**さらに、アンマウントしてから`cdrdao`を起動するまで間を空けると、macOSが自動的に再マウントしてしまい失敗する**（実機で再現・確認済み）。`MainWindow._start_cdrdao_rip()`は、ユーザーへの確認ダイアログの直後、`unmount_disk()`→`scan_bus()`/`find_scsi_device()`→ワーカー起動までを他の処理を挟まず連続して行うこと。完了時（成功・失敗・中断いずれも）は`disk_utils.mount_disk()`で必ず再マウントを試みる（`MainWindow._on_finished()`）。
   - **進捗表示（実機で確認済み）**: `cdrdao`はトラック一覧やサブチャンネル読み取り状況等のログは出力するが、機械可読な進捗率（パーセント）は出力しない。そのため進捗バーは`hdiutil makehybrid`と同様、不確定（ビジー）表示のままにする。
   - **実機での動作確認**: 実際のASUS SDRW-08U9M-U（USB接続）・20トラック/約564MB/約53分の音楽CDで、`--paranoia-mode 3`によるフルパラノイア読み取り全体が成功することを確認済み（所要時間約13分、`.bin`のサイズがdiskutilの報告するディスクサイズと完全一致）。
10. **AccurateRip照合（v1のみ）**: 詳細設計は[docs/design/accuraterip.md](docs/design/accuraterip.md)を参照。「厳密な検証」チェックボックス（`verify`）がONの場合、既存の自己一致検証に加え、リッピング完了後に一括で[AccurateRip](http://www.accuraterip.com/)への照合を試みる。
    - `accuraterip.py`がID計算・ネットワーク問い合わせ・CRC v1のオフセット探索（前置和による高速化、実機で動作確認済み）を担当し、UIフレームワーク・ファイルI/Oに依存しない。
    - `audio_cd.rip_and_convert_disc()`は、`verify=True`の場合のみ各トラックの一時WAVを即座に削除せず保持し、全トラックのリッピング完了後に第2パスとしてAccurateRip照合をまとめて行い、最後に全WAVを削除する（`read_pcm_samples`でPCMサンプルを読み込む）。
    - ディスクの最初・最後のトラックは照合の対象外（端点のトリミング規則が実データで未確認のため）。中間トラックのみ`TrackOutcome.accuraterip_confidence`に信頼度が設定される（`None`は「未実施」、「不一致」ではない）。
    - `config.AccurateRipSettings.search_range_samples`（既定1000）で探索範囲を設定可能。
    - CRC v2は正確な式を実データで特定できなかったため未実装（詳細はdocs/design/accuraterip.mdの3.4節）。
11. **設定のTOML反映・保存**: [config.py](src/mkhybrid_gui/config.py) はアプリ内部のチューニング値（`MediaSizeThresholds`/`AudioRipSettings`/`AccurateRipSettings`）に加え、GUI上のオプション選択を次回起動時にも復元するための `UiPreferences`（出力先フォルダ・Joliet/Rock Ridge/UDF・書き出し形式・検証有無）を保持する。
   - `config.get_config()` は既定パス（環境変数 `XDG_CACHE_HOME`（未設定時は `~/.cache`）配下の `mkhybrid/config.toml`。環境変数 `MKHYBRID_GUI_CONFIG` でパス自体を上書き可）から読み込み、`config.save_config()` は同じパスへTOMLとして書き戻す（`config.to_toml_string()` が手書きのシリアライザ。標準ライブラリの `tomllib` は読み込み専用のため）。設定はXDG的には本来 `XDG_CONFIG_HOME` に置くのがより適切だが、本プロジェクトの要件により `XDG_CACHE_HOME` 配下を使用する（`config.default_config_path()`）。
   - GUIはメイン画面（`MainWindow`）を `QTabWidget` で「ISO作成 / 音楽CD」タブと「設定」タブの2タブに分割する（`_build_main_tab`/`_build_settings_tab`）。「設定」タブでは `MediaSizeThresholds`/`AudioRipSettings`（サイズ閾値はMB単位のスピンボックスで表示、内部はバイトへ換算）を編集でき、「設定を保存」ボタン（`_on_settings_save_clicked`）で即座にTOMLへ反映・保存できる。設定ファイルの実際の場所も同タブに表示する（`config.get_config_path()`）。
   - `MainWindow.__init__()` はウィジェット構築直後に `config.get_config()` を読み込んで両タブの各ウィジェット（Joliet/Rock Ridge/UDFチェックボックス、検証チェックボックス、書き出し形式ラジオボタン、出力先ダイアログの初期フォルダ、設定タブのスピンボックス群）へ反映し（`_apply_config`）、`closeEvent()`（ワーカー実行中でない場合のみ）で両タブの現在の状態をまとめて保存する（`_save_current_settings`、内部で `_collect_current_config` を使用）。設定ファイルへの書き込みに失敗しても（権限不足等）アプリの終了自体は妨げない。
   - `last_output_directory` は「参照…」ダイアログの初期フォルダとしてだけでなく、音楽CD選択時（`_on_device_changed`でis_audio判定時）に出力先フォルダ欄が空であれば、その初期値としても直接反映すること。ダイアログの初期フォルダとしてのみ使い、欄自体に反映しないと、「前回と同じフォルダに書き出したいだけなのに毎回「参照…」を押し直す必要がある」という実機での報告どおりの不満につながる（`tests/test_main_window.py`の`test_selecting_audio_cd_prefills_output_folder_from_last_directory`で検証済み）。ISO作成では出力先はファイル名まで必要なため、この補完はis_audioの場合のみに限ること。
   - `audio_format` は表示ラベルではなく `AudioFormat` のメンバー名（例: `"ALAC"`）で保存する。読み込み時に未知の値であれば `AudioFormat.ALAC` にフォールバックする。
   - 「設定」タブには、`last_output_directory` を表示専用（読み取り専用）で見せる欄と、Finderの標準的なフォルダ選択ダイアログで変更できる「参照…」ボタンを置く（`settings_output_dir_edit`/`_on_settings_output_dir_browse_clicked`）。ここで変更した値は、現在「作成」タブが音楽CDモードであれば出力先フォルダ欄にも即座に反映する（ISO作成モードでは出力先はファイル名まで必要なため上書きしない）。これが無いと「音楽CDの出力先を変えたいだけなのに、一度ドライブを選び直さないと変更できない」という不満につながる（実機での報告により追加）。

## テスト

- `disk_utils.py` のパース処理・メディア種別判定（`detect_media_type`）は `diskutil list -plist` / `diskutil info -plist` のサンプル出力を固定データとして用意し、ユニットテストでカバーする。
- `iso_builder.py` は実際のCD-ROMを使わず、`subprocess.run`/`subprocess.Popen` をモック化してコマンド組み立て・実行結果処理のみを検証する。
- `audio_cd.py` は実際の音楽CD・cd-paranoia/afconvert/flacバイナリを使わず、`subprocess.run`/`subprocess.Popen` をモック化してトラック数解析・コマンド組み立て・検証ロジック（複数回読み取りの一致判定）・変換処理を検証する。ただし `write_metadata_tags`（`mutagen`）は外部バイナリに依存しない純Pythonのため、モックせず実際に妥当なフォーマットの最小限のファイル（`wave`標準モジュールやバイト列を直接組み立てて生成、Homebrew依存の`flac`バイナリや非推奨の`aifc`モジュールは使わない）を用意してタグの読み書きをテストする。
- `metadata.py` の `compute_disc_id` は、実際にMusicBrainz APIへ問い合わせて確認した実データ（disc id・offsets・sectors）をテストベクタとして使う（当てずっぽうの値やlibdiscid由来の値を使わない）。
- `musicbrainz.py` は実ネットワークを使わず、`url_opener` を差し替えたフェイクレスポンスで正常系（0/1/複数件）・HTTPエラー・タイムアウト・不正JSONを検証する。
- `accuraterip.py`（`compute_ids`/`build_query_url`/`lookup`）も同様に実ネットワークを使わず、`url_opener`を差し替えたフェイクバイナリレスポンス（`_HEADER_STRUCT`/`_ENTRY_STRUCT`と同じ構造で自作）で検証する（`tests/test_accuraterip.py`）。`compute_ids`のテストベクタは、実際のAccurateRipサーバーで確認した実データではなく、実装済みの計算式から手計算で導出した自作TOCによるspec-conformanceテストである点に注意（元になった実データの生TOCは保存されておらず再現できないため。次に実機で確認する機会があれば、実データによるテストベクタに置き換えることが望ましい）。`metadata.compute_disc_id`（MusicBrainz、`LEAD_IN_FRAMES`加算あり）とオフセット規約が異なることを直接検証するテストも含む。`search_offset_v1`（CRC v1のオフセット探索、実機で動作確認済み）は、前置和による高速版（`_fast_v1`）が素朴なO(L)参照実装（テスト内に直接書く）とランダムデータで一致することを検証する。
- `audio_cd.read_pcm_samples`（AccurateRip用のPCM読み込み）は、既知の左右チャンネル値から生成した最小WAVで、サンプルの結合（`(right<<16)|left`）とチャンネル数/サンプル幅/サンプルレートの検証を確認する。`rip_and_convert_disc`のAccurateRip統合は、`accuraterip.lookup`をモックし、中間トラックのみ`accuraterip_confidence`が設定されること・先頭/最終トラックは対象外のままであること・`verify=False`時は挙動が変わらない（一時WAVが即座に削除される）ことを検証する。
- GUI部分のテストには `pytest-qt`（`qtbot`）を用いる（[tests/test_main_window.py](tests/test_main_window.py)）。`IsoWorker`/`AudioRipWorker`/`MetadataLookupWorker`は実際に起動せず、`list_volumes`/`query_disc_toc`/`missing_tools`等の呼び出し境界をモック化し、ウィジェットの表示切り替え・入力検証・状態管理のロジックのみを検証する。`QMessageBox`はモーダルダイアログのためstaticメソッドを差し替え、テストがブロックされないようにする。ワーカースレッドを実際に起動して完了まで待つ結合テストは対象外（README.md の「ロードマップ・既知の制限」で追跡している）。
- 実機（実CD-ROM/DVD/BD/音楽CD）を使った結合テストはCI対象外とし、手動確認手順をREADMEに記載する。

## やってはいけないこと

- Windows/Linux上での動作を前提にしたコード分岐を追加しない（本ツールはmacOS専用）。
- `hdiutil`/`diskutil` の出力形式変更に備え、テキストパースではなく `-plist` 出力（`plistlib`でパース）を優先する。
- ユーザーの許可なくディスクのアンマウント・イジェクトを自動実行しない（GUI上で明示的な確認ダイアログを挟むこと）。
- BDXL・M-DISCを専用のメディア種別として個別分岐しない（通常のBD/DVDと同じ経路で処理できるため、サイズベースの判定に任せる）。
- `cd-paranoia` に `-Z`（パラノイア無効化）を指定しない。誤り訂正・再読込を無効化してしまい「正確なリッピング」の要件を満たせなくなる。
- 音楽CDのトラック書き出しをCDDAFSマウント経由の単純なファイルコピーに戻さない（誤り訂正・検証ができず、過去の実装がまさにこの理由で置き換えられた）。
- `cd-paranoia`/`flac` が見つからない場合に、フォーマット変換をサイレントにスキップしたり、CDDAFSコピー等の低精度な代替手段に自動フォールバックしたりしない。GUI上で明確にエラー表示し、`brew install` を案内すること。
- `audio_cd.effective_path()` を「`PATH`が空の場合だけログインシェルを問い合わせる」実装に戻さない。GUI起動時も`PATH`は非空（launchdの最小値）になるため、その条件では常にHomebrewのコマンドが見つからなくなる。
- `cdrdao.py`に、`audio_cd.effective_path()`/`tool_path()`/`command_env()`と同じPATH解決ロジック（ログインシェルのPATHを常にマージする処理）を再実装しない。`cdrdao.py`はこれらを`audio_cd`から直接importして再利用すること。同じ回帰しやすいロジックを2箇所に増やすと、片方だけ修正されもう片方が古いままになるリスクが上がる。
- `cdrdao.build_read_cd_command()`から`--paranoia-mode 3`を外したり、より弱いパラノイアモードの値に変更したりしない。これは`cd-paranoia`に`-Z`を渡さない既定動作と同じ位置づけの要件（誤り訂正・再読込を最大限有効にする）であり、省略すると「正確なバックアップ」の要件を満たせなくなる。
- `cdrdao.CdrdaoWorker`に`progress_percent`相当のシグナルを追加し、確定的な進捗率を偽装しない。`cdrdao`は機械可読な進捗率を出力しないことを実機で確認済みのため、進捗バーは`hdiutil makehybrid`と同様に不確定（ビジー）表示のままにすること。
- `MainWindow._start_cdrdao_rip()`で、`unmount_disk()`の呼び出しと`CdrdaoWorker`の起動（`cdrdao.scan_bus()`/`find_scsi_device()`を含む）の間に、ユーザー操作を待つダイアログや他の重い処理を挟まない。アンマウントしてから`cdrdao`が実際にドライブへアクセスするまでに間が空くと、macOSが自動的に再マウントしてしまい`cdrdao`が"Device already in use"で失敗する（実機で再現・確認済みの回帰）。
- `cdrdao`の`--device`に`/dev/rdiskN`（`disk_utils.whole_disk_raw_device()`の戻り値）を渡す実装に戻さない。`cdrdao`はmacOSでは`cdrdao scanbus`が返すIOKitレジストリパスしか受け付けず、`/dev/rdiskN`を渡すと`Cannot setup device`で必ず失敗する（実機で確認済み）。
- `MainWindow._on_finished()`から、`_cdrdao_unmounted_device_identifier`が設定されている場合の`mount_disk()`呼び出しを削除しない。省略すると、cdrdao実行後にディスクがアンマウントされたままになり、Finder等から見えなくなる。
- `audio_cd.rip_and_convert_disc()`で、`verify=True`時にトラックの一時WAVを（従来のように）変換直後に即座に削除する実装に戻さない。AccurateRip照合には前後トラックの境界サンプルが必要で、全トラック終了後の第2パスで初めて読み込む。削除は第2パス完了後にまとめて行うこと（詳細は[docs/design/accuraterip.md](docs/design/accuraterip.md)の4.1節）。
- `accuraterip.py`のCRC v2（`crc_v2`）照合を、確認されていない式のまま実装しない。実データで2種類の候補式（per-term fold・末尾一括fold）を試したがいずれも一致しなかった（詳細はdocs/design/accuraterip.mdの3.4節）。新しい式を試す場合も、必ず実機データでの検証を経ること。
- `accuraterip`によるAccurateRip照合を、ディスクの最初・最後のトラックにも適用しない。端点のトリミング規則が実データで確認できていないため、`audio_cd.rip_and_convert_disc()`は中間トラック（2番目〜最後から2番目）のみを対象にすること。
- `accuraterip.search_offset_v1()`/`_fast_v1()`の乗数を、探索するオフセット仮説（窓の開始位置）から計算する実装に戻さない。乗数は窓の中でのローカル位置（1始まり）でなければならず、絶対位置から計算すると実機検証で発見した回帰（オフセットを変えるたびに乗数がずれ、常に不一致になる）が再発する。
- `iso_builder.verify_iso()` を `subprocess.run`（完了を待ってから出力をまとめて渡す方式）に戻さない。検証中のGUI進捗表示が完了までフリーズしたように見える回帰になる。
- `iso_builder.verify_iso()` を `hdiutil verify` ベースの実装に戻さない。`hdiutil makehybrid` が生成するイメージにはチェックサムが一切含まれないため（実機確認済み）、`hdiutil verify` は正常なISOイメージに対しても必ず失敗し、ISO作成が実際には成功しているのに毎回「失敗」と誤報告する重大な回帰になる。
- `IsoWorker.run()` から `verify_iso()` への `source=self._source` の受け渡しを削除しない。UDFを含まない（ISO9660/Jolietのみの、CD選択時の既定）イメージでは、`source` が無いと検証が「attachできたことのみ確認」まで後退し、切り詰め・内容破損があっても検出できなくなる（実機での報告により発見・修正済みの回帰）。
- 実行中のワーカースレッド（`IsoWorker`/`AudioRipWorker`）を残したままウィンドウを閉じられるようにしない（`MainWindow.closeEvent` のガードを外さない）。
- `disk_utils.detect_media_type()` を、`filesystem_type` が既知の非光学ファイルシステム（`apfs`/`hfs+`/`exfat` 等）と判明している場合にもサイズだけでCD/DVD/BDと判定するように戻さない。サイズベースの推定フォールバックは `filesystem_type is None`（＝真に判別材料がない場合）に限ること。これを怠ると、内蔵の起動ディスク（Macintosh HD等）がGUIのドライブ選択肢に「Blu-ray」「データCD」等として表示され、誤って選択できてしまう（実機のMacで実際に再現・修正済みの回帰）。
- MusicBrainzへの問い合わせ（`musicbrainz.lookup_releases`）を、ユーザーの明示的なボタン操作なしに自動実行しない（デバイス選択時やアプリ起動時に暗黙で通信を発生させない）。
- MusicBrainz APIのレート制限（1秒1リクエスト）を無視して連続で問い合わせない。IPアドレスがブロックされるリスクがある。`musicbrainz._wait_for_rate_limit` を経由しないネットワーク呼び出しを追加しない。
- `metadata.compute_disc_id()` の実装を、実データで検証した仕様（オフセットは生LBAに `LEAD_IN_FRAMES`(150)を加算したフレーム値、SHA-1 + Base64の`+/=`を`._-`に置換）から離れた形に変更しない。1文字でもズレると生成されるDisc IDが全く別物になり、無音で「見つかりません」という結果になる（検出しにくいバグになるため要注意）。
- `MainWindow.__init__()` から `self._on_device_changed(self.device_combo.currentIndex())` の明示呼び出しを削除しない。マウント済みドライブが1つも無い場合（`list_volumes()`が空リストを返す場合）、`device_combo`は空のままで`currentIndexChanged`シグナルが一度も発火しないため、この明示呼び出しが無いと音楽CD用のメタデータ入力欄（アルバム名/アーティスト名/トラック名テーブル等）が、何も選択されていないのに表示されたままになる回帰になる（`tests/test_main_window.py`の`test_metadata_widgets_hidden_by_default`で検出・修正済み）。
- `_start_iso_build()`のオプション検証（「Joliet/UDFのいずれかは有効に」）に`options.rock`（Rock Ridgeチェックボックス）を含めない。`-rock`は`hdiutil makehybrid`に一切渡せず`-iso`指定時に自動的に有効になるだけの値なので、「Rock Ridgeだけ有効」と「Joliet/Rock Ridge/UDF全て無効」は生成されるコマンドが完全に同一（`-iso`のみ）になる。`rock`を判定に含めると、この2つの同一の状態のうち片方だけがエラーになるという一貫性のない挙動になる（実機での報告により発見、`tests/test_main_window.py`の`test_start_iso_build_rock_ridge_alone_is_treated_like_no_options`で検出・修正済み）。
- 「設定ファイルの場所」等、長さが可変・予測できない文字列を表示するラベルを`QFormLayout.addRow(caption, widget)`の値カラムに置かない。`QFormLayout`の値カラムの実効幅はウィンドウ幅に対して狭くなりがちで、長いパス文字列が折り返されずに途中で見切れる（実機で報告・修正済み）。タブの全幅を使う独立した行として配置すること（`settings_path_label`を参照）。
- `audio_cd.rip_and_convert_disc()`を、アルバム名未入力時に出力先フォルダ直下へ直接書き出す（サブディレクトリを作らない）実装に戻さない。アルバム名・`fallback_folder_name`（ディスクのボリューム名）のいずれも指定されない場合に限り、従来通り出力先フォルダ直下に書き出してよい。`MainWindow._start_audio_rip()`は`Volume.volume_name`（無ければ`device_identifier`）を`fallback_folder_name`として必ず渡すこと。これを怠ると、アルバム名を入力し忘れた場合に複数回のリッピング結果が出力先フォルダ直下で無秩序に混在する回帰になる（実機での報告により発見、`tests/test_audio_cd.py`の`test_rip_and_convert_disc_uses_fallback_folder_name_without_album`で検出・修正済み）。

## コミット/PR規約

- コミットメッセージは日本語または英語のいずれかで統一し、変更内容が分かる粒度で分割する。
- GUI変更を伴うPRには、変更前後のスクリーンショットを添付する。

## AIコーディングエージェントの作業方針（確認の省略可否）

- 破壊的操作・当初の依頼範囲を超えるスコープ拡大・安全ガードレールへの
  抵触のいずれにも当たらない限り、都度ユーザーに確認を取らずに作業を
  進めてよい（テスト実行、依存関係のインストール、依頼範囲内のファイル
  読み書き、実機・実デバイスに対する読み取り専用の確認コマンド実行等）。
- 一方で、`git push --force`（force-push）、`git commit --amend`、
  `--no-verify`（フックのスキップ）等、force-push/--amend/--no-verifyに
  類する操作や、その他ユーザー自身の明示的な指示を要求する操作について
  は、このプロジェクトでも例外なく毎回確認を取ること。これは既存の
  Git安全プロトコル（破壊的操作の確認、フックのスキップ禁止等）を
  緩和するものではない。
- 通常の（force-pushではない）`git commit`・`git push origin main`は、
  一区切りついた作業（テストが通っている状態）のたびに、都度確認を
  取らず自動的に実行してよい（ユーザーからの明示的な指示による）。
  コミットメッセージは変更内容が分かる粒度で作成し、pushしたコミット
  ハッシュを作業報告に含めること。
