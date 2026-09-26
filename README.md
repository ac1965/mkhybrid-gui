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
