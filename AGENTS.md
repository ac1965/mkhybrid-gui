# AGENTS.md

このリポジトリは、macOS上で光学ディスク（データCD/音楽CD/DVD/Blu-ray）からWindows/Linuxでも読めるISOイメージ（ISO 9660 + Joliet + Rock Ridge + 任意でUDF）を作成するための、Pythonベース GUIアプリケーションです。AIコーディングエージェントがこのプロジェクトを扱う際は、以下の方針に従ってください。

## プロジェクト概要

- **パッケージ名**: `mkhybrid-gui`（PyPI配布名・リポジトリ名）。Python内の `import` 名はハイフンを使えないため、モジュール名は `mkhybrid_gui`（アンダースコア表記）とする。
- **目的**: macOSに接続した光学ドライブの内容を、`hdiutil makehybrid` を用いて Windows/Linux 双方で読み取り可能なハイブリッドISOイメージに変換するGUIツールを提供する。
- **対応メディア**: データCD / 音楽CD（Audio CD） / DVD / Blu-ray（BD）。BDXL（大容量BD）・M-DISC（アーカイブ用メディア）は、OS上は通常のBD-R/BD-REと同じファイルシステムでマウントされるため、追加のメディア種別分岐は不要（[disk_utils.py](src/mkhybrid_gui/disk_utils.py) の `detect_media_type` はサイズベースの判定でこれらを自然にカバーする）。
  - データCD/DVD/BD: `hdiutil makehybrid` でISOイメージ化（ISO9660 + Joliet + Rock Ridge、DVD/BDでは大容量ファイル対応のUDFを追加可能）。
  - 音楽CD: macOSがCDDAFSで各トラックを再生可能なAIFFファイルとしてマウントする仕組みを利用し、[audio_cd.py](src/mkhybrid_gui/audio_cd.py) がそれらをフォルダへコピーする。ビット単位で完全なRed Book CDイメージ（BIN/CUE等）の作成は対象外（外部ツール依存を避けるため）。
- **対象OS**: macOS専用（`hdiutil` / `diskutil` はmacOS標準コマンドに依存するため、Windows/Linuxでは動作しない）。
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

- **外部コマンド呼び出し**: `subprocess`（`diskutil list`, `diskutil info`, `hdiutil makehybrid`, `hdiutil verify`）。音楽CDのトラック書き出しは外部コマンドではなく、CDDAFSマウントポイントからの `shutil` ファイルコピーで行う。
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
│       ├── disk_utils.py       # diskutil list/info のパース、メディア種別（MediaType）判定
│       ├── iso_builder.py      # hdiutil makehybrid / verify のラッパー、IsoWorker(QThread)
│       ├── audio_cd.py         # 音楽CD（CDDAFS）のトラック書き出し、AudioRipWorker(QThread)
│       └── ui/
│           └── main_window.py  # PySide6ウィジェット定義（メディア種別に応じてUIを切替）
├── tests/
│   ├── test_disk_utils.py
│   ├── test_iso_builder.py
│   └── test_audio_cd.py
└── mkhybrid_gui.spec        # PyInstaller用
```

## セットアップ・実行コマンド

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # PySide6 を含む
python -m mkhybrid_gui.app
```

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
- ビジネスロジック層（`disk_utils.py`, `iso_builder.py`, `audio_cd.py`）はUIフレームワーク（PySide6）に依存しない設計とし、単体でテスト可能にする。

## 主要な実装要件（機能仕様）

1. **デバイス/ドライブ選択**: `diskutil list` の結果からマウント済みボリュームの一覧をGUI上に表示し、ユーザーに選択させる。各ボリュームは `diskutil info -plist` の `FilesystemType` とサイズから `MediaType`（データCD/音楽CD/DVD/BD）を推定し、選択肢のラベルに表示する（`disk_utils.detect_media_type`）。
2. **イメージ作成（データCD/DVD/BD）**: 選択対象に対して以下を実行する。
   ```
   hdiutil makehybrid -iso -joliet -rock [-udf] -o <出力先.iso> <デバイスorマウントポイント>
   ```
   - `-rock` はデフォルトでON、詳細オプション（Jolietのみ等）はGUI上のチェックボックスで切り替え可能にする。
   - `-udf` はDVD/BD（BDXL・M-DISCを含む）選択時にデフォルトON（ISO9660の4GBファイルサイズ上限を回避するため）、CD選択時はデフォルトOFF。ユーザーは任意に変更できる。
3. **音楽CDの書き出し**: `MediaType.CD_AUDIO` を選択した場合、ISO作成UIの代わりに出力先フォルダ選択に切り替え、CDDAFSマウントポイント配下の `*.aiff` トラックを `audio_cd.extract_audio_tracks` でフォルダへコピーする（`AudioRipWorker`）。
4. **進捗表示**: `IsoWorker` / `AudioRipWorker`（いずれも `QThread`）で非同期実行し、`progress` シグナルでメインスレッドのUIを更新する。処理中はGUIをブロックしないこと。
5. **検証**: ISOイメージ作成後に `hdiutil verify <出力先.iso>` を自動実行し、結果をGUIに表示する（音楽CDの書き出しには対応する検証コマンドがないため、コピー成否のみ報告する）。
6. **エラーハンドリング**: コピーガード付きメディア等でセクタ単位読み取りが必要なケースを検出できない場合は、明確なエラーメッセージを表示し、対処法（別ツールの利用など）を案内する。

## テスト

- `disk_utils.py` のパース処理・メディア種別判定（`detect_media_type`）は `diskutil list -plist` / `diskutil info -plist` のサンプル出力を固定データとして用意し、ユニットテストでカバーする。
- `iso_builder.py` は実際のCD-ROMを使わず、`subprocess.run`/`subprocess.Popen` をモック化してコマンド組み立て・実行結果処理のみを検証する。
- `audio_cd.py` は実際の音楽CDを使わず、CDDAFSマウントを模した一時ディレクトリ（`.aiff`ファイル）でトラック検出・コピー処理を検証する。
- GUI部分のテストには `pytest-qt`（`qtbot`）を用いる。
- 実機（実CD-ROM/DVD/BD/音楽CD）を使った結合テストはCI対象外とし、手動確認手順をREADMEに記載する。

## やってはいけないこと

- Windows/Linux上での動作を前提にしたコード分岐を追加しない（本ツールはmacOS専用）。
- `hdiutil`/`diskutil` の出力形式変更に備え、テキストパースではなく `-plist` 出力（`plistlib`でパース）を優先する。
- ユーザーの許可なくディスクのアンマウント・イジェクトを自動実行しない（GUI上で明示的な確認ダイアログを挟むこと）。
- 音楽CDのビット単位完全複製（Red Book CDイメージ、BIN/CUE等）のために `cdrdao`/`xorriso` 等のmacOS標準外の外部コマンドを依存として追加しない（AIFFトラック書き出しのみをスコープとする）。
- BDXL・M-DISCを専用のメディア種別として個別分岐しない（通常のBD/DVDと同じ経路で処理できるため、サイズベースの判定に任せる）。

## コミット/PR規約

- コミットメッセージは日本語または英語のいずれかで統一し、変更内容が分かる粒度で分割する。
- GUI変更を伴うPRには、変更前後のスクリーンショットを添付する。
