# mkhybrid-gui

macOS上に接続した光学ドライブ（データCD / 音楽CD / DVD / Blu-ray）の内容を、
`hdiutil makehybrid` を用いてWindows/Linux 双方で読み取り可能なハイブリッドISOイメージ
（ISO 9660 + Joliet + Rock Ridge、必要に応じてUDF）に変換するためのGUIツールです。

対象OSはmacOS専用です（`hdiutil` / `diskutil` に依存するため）。

## 対応メディア

| メディア種別 | 出力 |
| --- | --- |
| データCD | ハイブリッドISOイメージ（ISO9660 + Joliet + Rock Ridge） |
| DVD / Blu-ray（BD） | 上記に加えUDFをデフォルトで有効化（4GB超のファイルに対応） |
| BDXL / M-DISC | 通常のBDと同じ経路で処理されます（OS上は区別なくマウントされるため） |
| 音楽CD（Audio CD） | `cdparanoia` による正確なリッピング（誤り訂正・再読込・検証付き）＋選択形式（ALAC/AIFF/FLAC/WAV/AAC）への変換 |

音楽CDは `cdparanoia` を使い、ジッター補正・誤り訂正・自動再読込を有効にした
パラノイアモードで読み取ります。さらに各トラックを独立して複数回リッピングし、
結果のチェックサムが一致するかどうかで読み取りの正確性を検証します（既定でON、
「厳密な検証」チェックボックスでOFFに変更可能）。取得したWAVは、選択した形式
（Apple Lossless推奨）に変換して保存します。

macOS標準のCDDAFSマウント（Finderが見せる再生可能なAIFFファイル）は、ドライブの
誤り訂正結果を検証する手段がないため使用していません。

## 必要な追加ツール（音楽CDの書き出しのみ）

データCD/DVD/BDのISOイメージ作成はmacOS標準コマンドのみで完結しますが、
音楽CDの正確なリッピングとFLAC書き出しには、Homebrewで以下を追加インストールする
必要があります。

```bash
brew install cdparanoia flac
```

- `cdparanoia`: 誤り訂正・再読込付きの正確なリッピングに必須（全フォーマット共通）。
- `flac`: FLAC形式で書き出す場合のみ必須。
- ALAC/AIFF/AACへの変換にはmacOS標準の `afconvert` を使用するため追加インストール不要です。

必要なツールが不足している場合、アプリは書き出し開始前にエラーダイアログで
不足しているコマンドとインストール方法を表示します。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mkhybrid_gui.app
```

## 使い方

### データCD / DVD / Blu-ray（BD・BDXL・M-DISC含む）

1. 「ドライブ/ボリューム」欄でディスクを選択します（一覧は `diskutil list -plist` から取得し、
   `diskutil info -plist` のファイルシステム情報とサイズからメディア種別を自動判定して表示します）。
2. 「出力先ISO」で保存先のファイルパスを指定します。
3. 必要に応じて Joliet / Rock Ridge / UDF のオプションを切り替えます
   （Rock RidgeとJolietはデフォルトON。UDFはDVD/BD選択時のみデフォルトONで、
   大容量ファイル対応のために使用します）。
4. 「ISOイメージを作成」をクリックすると、バックグラウンドで
   `hdiutil makehybrid` によるイメージ作成と `hdiutil verify` による検証が実行されます。
   処理中もGUIはブロックされません。

コピーガード付きメディア等、セクタ単位の読み取りが必要なディスクには対応していません。
その場合はエラーメッセージが表示されるので、専用ツールの利用を検討してください。

### 音楽CD（Audio CD）

1. 「ドライブ/ボリューム」欄で音楽CDを選択すると、自動的に「音楽CD」と判定され、
   出力先の指定がフォルダ選択に切り替わります。
2. 「書き出し形式」でALAC（推奨）/AIFF/FLAC/WAV/AACから選択します。
3. 「厳密な検証」（既定ON）を有効にすると、各トラックを複数回リッピングして
   結果が一致するか比較します。処理時間は約2倍になりますが、読み取りミスの
   検出精度が上がります。
4. 「出力先フォルダ」を指定し、「オーディオトラックを書き出す」をクリックすると、
   `cdparanoia` によるリッピングと選択形式への変換が実行されます。
   一部トラックが複数回読み取っても一致しなかった場合は、完了メッセージで
   「未検証」として警告されます。

## テスト

```bash
make test
# または
pip install -e ".[dev]"
pytest
```

- `disk_utils.py` のパース処理・メディア種別判定は `diskutil list -plist` / `diskutil info -plist` の固定サンプルデータでテストします。
- `iso_builder.py` は `subprocess` をモック化し、実際のCD-ROM／hdiutilなしにコマンド組み立てを検証します。
- `audio_cd.py` は実際のcdparanoia/afconvert/flacを使わず、`subprocess` をモック化して
  トラック数解析・コマンド組み立て・複数回読み取りの一致検証ロジック・変換処理を検証します。
- GUI部分は `pytest-qt` を使用します。
- 実ディスクを使った結合テストはCI対象外です。手動で以下を確認してください（音楽CDのテストには
  事前に `brew install cdparanoia flac` が必要です）。
  1. 物理光学ドライブをMacに接続し、アプリ上で一覧にメディア種別付きで表示されることを確認する。
  2. データCD/DVD/BDでISO作成〜検証が正常に完了することを確認する。
  3. 生成されたISOをWindows/Linux環境でマウントし、ファイルが読み取れることを確認する。
  4. 音楽CDで各形式（ALAC/AIFF/FLAC/WAV/AAC）が正しく書き出され、再生できることを確認する。
  5. 音楽CDで「厳密な検証」を有効にした場合に、トラックが検証済みとして完了することを確認する
     （スクラッチ等がある盤では未検証警告が出ることも確認する）。

## Makefile

```bash
make build      # .venv を用意し、PyInstallerで .app をビルド
make test       # .venv を用意し、pytest を実行
make install    # build を実行し、.app を $(PREFIX)（既定: /Applications）へインストール
make clean      # build/dist/キャッシュ等の生成物を削除（.venvは残す）
make distclean  # clean に加えて .venv も削除
```

## .app化（PyInstaller）

```bash
pip install pyinstaller
pyinstaller mkhybrid_gui.spec
```

ビルド後、`dist/mkhybrid-gui.app` 内にQtプラットフォームプラグイン
（`libqcocoa.dylib` 等）が同梱されているか確認してください。
配布時はコード署名なしのadhoc署名で構いません。

## ライセンス

本ツールは [PySide6](https://doc.qt.io/qtforpython/)（LGPLv3）を使用しています。
