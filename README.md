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
| 音楽CD（Audio CD） | `cd-paranoia` による正確なリッピング（誤り訂正・再読込・検証付き）＋選択形式（ALAC/AIFF/FLAC/WAV/AAC）への変換 |

音楽CDは `cd-paranoia` を使い、パラノイアモード（`-Z` を指定しない既定動作）で読み取ります。
さらに各トラックを独立して複数回リッピングし、結果のチェックサムが一致するかどうかで
読み取りの正確性を検証します（既定でON、「厳密な検証」チェックボックスでOFFに変更可能）。
取得したWAVは、選択した形式（Apple Lossless推奨）に変換して保存します。

macOS標準のCDDAFSマウント（Finderが見せる再生可能なAIFFファイル）は、ドライブの
誤り訂正結果を検証する手段がないため使用していません。

## 必要な追加ツール（音楽CDの書き出しのみ）

データCD/DVD/BDのISOイメージ作成はmacOS標準コマンドのみで完結しますが、
音楽CDの正確なリッピングとFLAC書き出しには、Homebrewで以下を追加インストールする
必要があります。

```bash
brew install libcdio-paranoia flac
```

`brew install` 済みでもGUIアプリ（Finder起動）から `cd-paranoia` / `flac` が
見つからない場合は、Homebrewのインストール先（Apple Siliconなら
`/opt/homebrew/bin`、Intelなら `/usr/local/bin`）を含むログインシェルの
PATHを都度取得して利用するため、通常は追加設定不要です。改善しても
見つからない場合は `echo $SHELL` で使用シェルを確認し、そのログインシェルの
設定ファイル（`.zprofile` 等）でPATHにHomebrewのbinディレクトリが
含まれているか確認してください。

## セットアップ・実行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m mkhybrid_gui.app
```

`make` を使う場合は次のターゲットが利用できます（`.venv` は自動的に用意されます）。

```bash
make build      # PyInstallerで.appをビルド
make test       # pytestを実行
make install    # buildを実行し、.appを$(PREFIX)（既定: /Applications）へインストール
make clean      # build/dist/キャッシュ等の生成物を削除（.venvは残す）
make distclean  # cleanに加えて.venvも削除
```

## 実行中の中断・終了について

ISO作成／音楽CDの書き出し中は、画面右側の「中断」ボタンから安全に処理を
停止できます。中断すると、実行中の外部コマンド（`hdiutil` / `cd-paranoia` /
`afconvert` / `flac`）へ終了要求を送り、作成途中の一時ファイルを削除した上で
停止します。

処理の完了・中断を待たずにウィンドウを閉じることはできません（バックグラウンド
スレッドが実行中のままアプリを終了すると、異常終了や書き出し途中ファイルの
破損につながるため）。閉じたい場合は先に「中断」ボタンで停止してください。

## 進捗表示について

- **ISOイメージ作成中**: `hdiutil makehybrid` はビルド中の進捗率を一切出力
  しないため、進捗バーは不確定（ビジー）表示のままになります。`hdiutil verify`
  による検証フェーズの開始時点で90%、完了時点で100%の確定表示に切り替わります。
  検証中の出力（ログ）は、以前は検証完了後にまとめて表示されていましたが、
  現在はリアルタイムにストリーミング表示されます。
- **音楽CDのリッピング中**: トラック単位（`現在のトラック番号 / 総トラック数`）
  の粗い進捗率を確定表示します。

## ロードマップ・既知の制限

- [ ] GUI（`MainWindow`）の自動テストは未整備です。`pytest-qt` を導入し、
      ボタン操作やダイアログ表示を含む結合テストを追加する。
- [ ] 中断は外部コマンドへ `terminate`（SIGTERM）を送る方式のため、
      コマンドがシグナルを無視するケースでは停止までに時間がかかることがある。
      必要に応じて一定時間後の `kill`（SIGKILL）へのエスカレーションを検討する。
- [ ] `hdiutil makehybrid` 自体が進捗率を提供しないため、ISO作成中の
      詳細な進捗表示（ファイル単位・バイト単位など）は今のところ実現できていない。
- [ ] コピーガード付きメディア等、セクタ単位の特殊な読み取りが必要なディスクには
      対応していない（エラーメッセージで案内するのみ）。
- [ ] 複数ドライブの同時（並行）処理には対応していない（1台ずつの逐次処理）。
- [ ] `.app` の配布は現状adhoc署名のみ。将来的な公証（notarization）対応は未定。

## ライセンス

本プロジェクト自体は MIT License です。GUIフレームワークとして使用している
[PySide6](https://pypi.org/project/PySide6/) は LGPLv3 の下で配布されています。
