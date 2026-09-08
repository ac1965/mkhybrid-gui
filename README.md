# mkhybrid-gui

macOS上に接続したCD-ROMドライブの内容を、`hdiutil makehybrid` を用いて
Windows/Linux 双方で読み取り可能なハイブリッドISOイメージ（ISO 9660 + Joliet + Rock Ridge）
に変換するためのGUIツールです。

対象OSはmacOS専用です（`hdiutil` / `diskutil` に依存するため）。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mkhybrid_gui.app
```

## 使い方

1. 「ドライブ/ボリューム」欄でCD-ROM等のボリュームを選択します（一覧は `diskutil list -plist` から取得）。
2. 「出力先ISO」で保存先のファイルパスを指定します。
3. 必要に応じて Joliet / Rock Ridge のオプションを切り替えます（デフォルトは両方ON）。
4. 「ISOイメージを作成」をクリックすると、バックグラウンドで
   `hdiutil makehybrid` によるイメージ作成と `hdiutil verify` による検証が実行されます。
   処理中もGUIはブロックされません。

コピーガード付きメディア等、セクタ単位の読み取りが必要なディスクには対応していません。
その場合はエラーメッセージが表示されるので、専用ツールの利用を検討してください。

## テスト

```bash
pip install -e ".[dev]"
pytest
```

- `disk_utils.py` のパース処理は `diskutil list -plist` の固定サンプルデータでテストします。
- `iso_builder.py` は `subprocess` をモック化し、実際のCD-ROM／hdiutilなしにコマンド組み立てを検証します。
- GUI部分は `pytest-qt` を使用します。
- 実CD-ROMを使った結合テストはCI対象外です。手動で以下を確認してください。
  1. 物理CD-ROMドライブをMacに接続し、アプリ上で一覧に表示されることを確認する。
  2. ISO作成〜検証が正常に完了することを確認する。
  3. 生成されたISOをWindows/Linux環境でマウントし、ファイルが読み取れることを確認する。

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
