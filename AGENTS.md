# AGENTS.md

このリポジトリは、macOS上で光学ディスク（データCD/音楽CD/DVD/Blu-ray）からWindows/Linuxでも読めるISOイメージ（ISO 9660 + Joliet + Rock Ridge + 任意でUDF）を作成するための、Pythonベース GUIアプリケーションです。AIコーディングエージェントがこのプロジェクトを扱う際は、以下の方針に従ってください。

## プロジェクト概要

- **パッケージ名**: `mkhybrid-gui`（PyPI配布名・リポジトリ名）。Python内の `import` 名はハイフンを使えないため、モジュール名は `mkhybrid_gui`（アンダースコア表記）とする。
- **目的**: macOSに接続した光学ドライブの内容を、`hdiutil makehybrid` を用いて Windows/Linux 双方で読み取り可能なハイブリッドISOイメージに変換するGUIツールを提供する。
- **対応メディア**: データCD / 音楽CD（Audio CD） / DVD / Blu-ray（BD）。BDXL（大容量BD）・M-DISC（アーカイブ用メディア）は、OS上は通常のBD-R/BD-REと同じファイルシステムでマウントされるため、追加のメディア種別分岐は不要（[disk_utils.py](src/mkhybrid_gui/disk_utils.py) の `detect_media_type` はサイズベースの判定でこれらを自然にカバーする）。
  - データCD/DVD/BD: `hdiutil makehybrid` でISOイメージ化（ISO9660 + Joliet + Rock Ridge、DVD/BDでは大容量ファイル対応のUDFを追加可能）。
  - 音楽CD: [audio_cd.py](src/mkhybrid_gui/audio_cd.py) が `cdparanoia`（誤り訂正・再読込付きの正確なリッピング）でWAVを取得し、`afconvert`（ALAC/AIFF/AAC）または `flac`（FLAC）でユーザー選択の形式に変換する。WAVはそのまま採用する。macOS標準のCDDAFSマウント（単純なAIFFコピー）は誤り訂正・検証ができないため使用しない。
- **対象OS**: macOS専用（`hdiutil` / `diskutil` はmacOS標準コマンドに依存するため、Windows/Linuxでは動作しない）。
- **追加の外部依存（Homebrew）**: `cdparanoia`（音楽CDの正確なリッピングに必須）、`flac`（FLAC書き出し時のみ必須）。これらはmacOS標準コマンドではないため、利用者に `brew install cdparanoia flac` の実行を求める。ISOイメージ作成（データCD/DVD/BD）はこれらに依存しない。
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

- **外部コマンド呼び出し**: `subprocess`（macOS標準: `diskutil list`, `diskutil info`, `hdiutil makehybrid`, `hdiutil verify`、`afconvert`。Homebrew依存: `cdparanoia`, `flac`）。使用前に `shutil.which` で有無を確認し、不足時はインストール方法（`brew install ...`）をGUIに明示する（`audio_cd.missing_tools`）。
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
│       ├── audio_cd.py         # cdparanoiaによる正確なリッピング、afconvert/flacでの形式変換、AudioRipWorker(QThread)
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
# 音楽CDの正確なリッピング・FLAC書き出しに必要（データCD/DVD/BDのISO作成には不要）
brew install cdparanoia flac

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
3. **音楽CDの正確なリッピング**: `MediaType.CD_AUDIO` を選択した場合、ISO作成UIの代わりに出力先フォルダ選択・書き出し形式（ALAC/AIFF/FLAC/WAV/AAC、既定はALAC）・検証チェックボックスに切り替える。以下の3要件を満たすこと。
   1. **正確な読み取り**: `cdparanoia` をパラノイアモード（`-Z` を指定しない）で実行し、ジッター補正・C2エラー利用を有効にする。
   2. **誤り訂正・再読込**: 上記はcdparanoia自体が内部で行う（再実装しない）。
   3. **検証**: 1トラックを独立して複数回（既定2回、不一致なら最大3回まで）リッピングし、WAVのSHA-256チェックサムが一致することを確認する（`audio_cd.rip_track_verified`）。一致しなければ最後の読み取りを「未検証」として採用し、完了メッセージで警告する。
   - 取得したWAVは `afconvert`（ALAC/AIFF/AAC）または `flac`（FLAC）でユーザー選択の形式に変換する（`audio_cd.convert_audio`）。WAV選択時は変換不要でそのまま採用する。
   - 実行前に `audio_cd.missing_tools` で必要な外部コマンドの有無を確認し、不足時は `brew install ...` の案内を表示して処理を開始しない。
4. **進捗表示**: `IsoWorker` / `AudioRipWorker`（いずれも `QThread`）で非同期実行し、`progress` シグナルでメインスレッドのUIを更新する。処理中はGUIをブロックしないこと。
5. **検証（ISO）**: ISOイメージ作成後に `hdiutil verify <出力先.iso>` を自動実行し、結果をGUIに表示する。
6. **エラーハンドリング**: コピーガード付きメディア等でセクタ単位読み取りが必要なケースを検出できない場合は、明確なエラーメッセージを表示し、対処法（別ツールの利用など）を案内する。

## テスト

- `disk_utils.py` のパース処理・メディア種別判定（`detect_media_type`）は `diskutil list -plist` / `diskutil info -plist` のサンプル出力を固定データとして用意し、ユニットテストでカバーする。
- `iso_builder.py` は実際のCD-ROMを使わず、`subprocess.run`/`subprocess.Popen` をモック化してコマンド組み立て・実行結果処理のみを検証する。
- `audio_cd.py` は実際の音楽CD・cdparanoia/afconvert/flacバイナリを使わず、`subprocess.run`/`subprocess.Popen` をモック化してトラック数解析・コマンド組み立て・検証ロジック（複数回読み取りの一致判定）・変換処理を検証する。
- GUI部分のテストには `pytest-qt`（`qtbot`）を用いる。
- 実機（実CD-ROM/DVD/BD/音楽CD）を使った結合テストはCI対象外とし、手動確認手順をREADMEに記載する。

## やってはいけないこと

- Windows/Linux上での動作を前提にしたコード分岐を追加しない（本ツールはmacOS専用）。
- `hdiutil`/`diskutil` の出力形式変更に備え、テキストパースではなく `-plist` 出力（`plistlib`でパース）を優先する。
- ユーザーの許可なくディスクのアンマウント・イジェクトを自動実行しない（GUI上で明示的な確認ダイアログを挟むこと）。
- BDXL・M-DISCを専用のメディア種別として個別分岐しない（通常のBD/DVDと同じ経路で処理できるため、サイズベースの判定に任せる）。
- `cdparanoia` に `-Z`（パラノイア無効化）を指定しない。誤り訂正・再読込を無効化してしまい「正確なリッピング」の要件を満たせなくなる。
- 音楽CDのトラック書き出しをCDDAFSマウント経由の単純なファイルコピーに戻さない（誤り訂正・検証ができず、過去の実装がまさにこの理由で置き換えられた）。
- `cdparanoia`/`flac` が見つからない場合に、フォーマット変換をサイレントにスキップしたり、CDDAFSコピー等の低精度な代替手段に自動フォールバックしたりしない。GUI上で明確にエラー表示し、`brew install` を案内すること。

## コミット/PR規約

- コミットメッセージは日本語または英語のいずれかで統一し、変更内容が分かる粒度で分割する。
- GUI変更を伴うPRには、変更前後のスクリーンショットを添付する。
