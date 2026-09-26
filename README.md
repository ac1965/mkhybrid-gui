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

## 音楽CDのメタデータ

音楽CDを選択すると、アルバム名・アーティスト名・年・各トラック名を入力できる
欄が表示されます。入力すると、書き出すファイル名が `01 - タイトル.拡張子`
になり（未入力のトラックは従来通り `TrackNN.拡張子`）、ファイル自体にも
タグ（アルバム名/アーティスト名/トラック名/年）が書き込まれます
（[mutagen](https://mutagen.readthedocs.io/) を使用。MP4/ALAC/AACはiTunes系
タグ、FLACはVorbis Comment、WAV/AIFFはID3v2タグとして埋め込まれます）。

「オンラインで検索（MusicBrainz）」ボタンを押すと、ディスクのTOC（トラック数・
各トラックの長さ）から計算した
[MusicBrainz Disc ID](https://musicbrainz.org/doc/Disc_ID_Calculation) を使って
[MusicBrainz](https://musicbrainz.org/) に問い合わせ、見つかったアルバム名・
アーティスト名・トラック名を入力欄へ自動反映します。**この通信はボタンを押した
ときにのみ発生し、自動では行われません**。送信されるのはディスクの識別情報
（トラック構成のみ）で、個人情報は含まれません。ネットワークに接続していない
場合や該当ディスクが見つからない場合は、手動で入力してください（入力しなくても
リッピング自体は通常通り行えます）。

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
  しないため、進捗バーは不確定（ビジー）表示のままになります。検証フェーズの
  開始時点で90%、完了時点で100%の確定表示に切り替わります。
  検証中の出力（ログ）は、以前は検証完了後にまとめて表示されていましたが、
  現在はリアルタイムにストリーミング表示されます。
- **音楽CDのリッピング中**: トラック単位（`現在のトラック番号 / 総トラック数`）
  の粗い進捗率を確定表示します。

## ISOイメージの検証について

作成したISOイメージは、`hdiutil verify` ではなく、実際にattach（マウント）した
上で `diskutil verifyVolume` によりファイルシステムの整合性を検証します。

`hdiutil makehybrid` が生成するイメージにはチェックサムが一切含まれないため、
`hdiutil verify` を使うと**正常に作成できたISOイメージに対しても必ず
「検証失敗」と表示されてしまう**ことが実機検証で判明しました
（`hdiutil imageinfo` で `Checksummed: false` を確認）。そのため本ツールでは、
イメージを実際にattachできるかどうか、UDFを含む場合は `diskutil verifyVolume`
（内部的に `fsck_udf`）でファイルシステム自体の整合性を確認しています。
なお、UDFを含まない（ISO9660/Jolietのみの）イメージについては、macOS側に
対応するファイルシステム検証ツールが存在しないため、attachできたことのみを
もって検証成功としています（下記「ロードマップ・既知の制限」を参照）。

## ロードマップ・既知の制限

- [x] GUI（`MainWindow`）の自動テストを`pytest-qt`（`tests/test_main_window.py`）
      で整備済み。ウィジェットの表示切り替え・入力検証・中断/終了・
      メタデータ入力欄の状態管理をカバーする（実際の`hdiutil`/`cd-paranoia`/
      MusicBrainzへの通信は行わず、`IsoWorker`/`AudioRipWorker`等の
      呼び出し境界をモック化）。ワーカースレッドの実行そのものを
      通しで検証する結合テストは今後の拡張候補。
- [ ] 中断は外部コマンドへ `terminate`（SIGTERM）を送る方式のため、
      コマンドがシグナルを無視するケースでは停止までに時間がかかることがある。
      必要に応じて一定時間後の `kill`（SIGKILL）へのエスカレーションを検討する。
- [ ] `hdiutil makehybrid` 自体が進捗率を提供しないため、ISO作成中の
      詳細な進捗表示（ファイル単位・バイト単位など）は今のところ実現できていない。
- [ ] UDFを含まない（ISO9660/Jolietのみの）ISOイメージは、macOS側に対応する
      ファイルシステム検証ツールがないため、attachできることの確認に留まり、
      ファイルシステム内部の整合性までは検証できない。
- [ ] コピーガード付きメディア等、セクタ単位の特殊な読み取りが必要なディスクには
      対応していない（エラーメッセージで案内するのみ）。
- [ ] 複数ドライブの同時（並行）処理には対応していない（1台ずつの逐次処理）。
- [ ] `.app` の配布は現状adhoc署名のみ。将来的な公証（notarization）対応は未定。
- [ ] 音楽CDの検証は「同じトラックを複数回自分で読み取って一致を確認する」方式のみ。
      [AccurateRip](http://www.accuraterip.com/)（他のリッピング利用者の
      チェックサムとオンラインDBで照合する、より強力な検証方式）への対応は
      未実装（今回のMusicBrainzメタデータ機能とは別フェーズとして検討する）。
- [ ] MusicBrainzで複数候補が見つかった場合の選択UIは、簡易的な一覧選択
      （`QInputDialog`）のみ。候補ごとのトラックリストのプレビュー等は
      今後の拡張候補。

## ライセンス

本プロジェクト自体は MIT License です。GUIフレームワークとして使用している
[PySide6](https://pypi.org/project/PySide6/) は LGPLv3 の下で配布されています。
