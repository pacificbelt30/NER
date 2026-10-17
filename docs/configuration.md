# 翻訳設定

[ドキュメントの目次](README.md)

## 設定ファイル

```sh
cp config_sample.yml config.yml
```

設定はYAMLで記述し、ルートをマッピングにします。[config_sample.yml](../config_sample.yml)には全方式の例があります。選んだ翻訳方式に必要な項目を設定してください。

- CLIは作業ディレクトリの `config.yml` を読みます。
- サーバは既定で `config.yml` を読み、`NER_CONFIG_PATH` で変更できます。
- Web UIの「翻訳方式」で、設定済みのエンジンに切り替えられます。
- Web UIの「設定」は保存期間の設定です。翻訳APIのURLや認証情報を編集する画面はありません。

## 共通項目

| 項目 | 既定値 | 内容 |
| --- | --- | --- |
| `PRIORITY` | 未指定時は `GOOGLE` | `GOOGLE` / `TEXTRA` / `INDEX` / `BERGAMOT` |
| `FONT` | `''` | 標準の日本語フォント。フォントファイルのパスも指定可能 |
| `RETRIES` | `1` | 一時的な通信障害・サーバエラーに対する追加の再試行回数 |
| `CACHE_SIZE` | `1024` | 文書内の翻訳結果キャッシュの上限。`0` で無効 |
| `MAX_CHUNKS` | `128` | 1ブロックを分割できるチャンク数の上限 |
| `ERROR_POLICY` | `preserve` | エラー箇所を保持してPDFを出力。`strict` は翻訳・配置エラーで停止 |

`FONT` の相対パスは作業ディレクトリが基準です。空文字なら外部の日本語フォントファイルは不要です。

`RETRIES` の対象は接続・タイムアウトなどの一時障害と、HTTP 429 / 500 / 502 / 503 / 504です。不完全な応答や、URL・コードの保護に失敗した応答は再試行の対象にしません。

キャッシュは文書ごとのLRU方式で、ブロックの役割・見出し文脈・保護対象・分割方法も区別します。成功した結果だけを保存します。保存済みのPDF履歴とは別の仕組みです。

## Google翻訳

```yaml
PRIORITY: 'GOOGLE'
GOOGLE:
  URL: 'https://translate.googleapis.com/translate_a/single'
  TIMEOUT: 60
  REQUEST_INTERVAL: 0
  CHUNK_CHARS: 2000
FONT: ''
```

現在の実装は上記エンドポイントへのHTTP接続です。Google Cloud Translationのサービスアカウント認証を扱う実装ではありません。オンライン翻訳では本文を接続先へ送信します。

## TexTra

```yaml
PRIORITY: 'TEXTRA'
TEXTRA:
  NAME: 'YOUR_ACCOUNT_NAME'
  KEY: 'YOUR_API_KEY'
  SECRET: 'YOUR_API_SECRET'
  URL: 'https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/'
  TIMEOUT: 60
  REQUEST_INTERVAL: 3
  CHUNK_CHARS: 2000
FONT: ''
```

[みんなの自動翻訳＠TexTra](https://mt-auto-minhon-mlt.ucri.jgn-x.jp/)で利用するアカウントとAPI情報を設定します。`NAME`・`KEY`・`SECRET` のサンプル値は使用できません。接続先は契約・用途に合う英日翻訳エンドポイントを指定してください。

認証はOAuth 1です。NERへのログインアカウントとは別に設定します。

## ローカル翻訳

IndexとBergamotの設定例、実行環境、モデルの準備は[ローカル翻訳](local-translation.md)を参照してください。ローカル方式はオンライン方式へ自動で切り替わりません。モデルの取得やビルドに必要なネットワーク利用と、翻訳時の通信は別です。

## 通信と分割の設定

| 項目 | Google | TexTra | Index | Bergamot |
| --- | ---: | ---: | ---: | ---: |
| `TIMEOUT` の既定値（秒） | 60 | 60 | 300 | 300 |
| `REQUEST_INTERVAL` の既定値（秒） | 0 | 3 | 0 | 対象外 |
| `CHUNK_CHARS` の既定値 | 2000 | 2000 | 2000 | 700 |

`REQUEST_INTERVAL` はHTTPリクエスト開始時刻の間隔です。最初のリクエストの前には待機しません。HTTP接続には文書内で再利用するセッションを使い、リダイレクトを追跡しません。

`CHUNK_CHARS` はブロック内の分割目安で、PythonのUnicodeコードポイント数を基準にします。書記素クラスタと保護トークンの途中は分割しません。文・節・単語を優先して分割し、ブロック単位に再結合します。仕組みは[PDF処理とエラー方針](pdf-layout.md)に記載しています。

## 設定が利用できない場合

Web UI/APIはジョブを受け付ける前に、選択したエンジンの設定を確認します。「設定済み」は設定内容の検証結果であり、モデルサーバや外部サービスへの疎通成功を保証する表示ではありません。

CLI・Pythonライブラリは、既定の `preserve` ではエンジンの準備に失敗しても原文と説明を含むPDFを出力します。指定フォントを使えない場合は標準フォントへ切り替え、注意を記録します。`strict` の動作や出力できないケースは[エラー時の出力](pdf-layout.md#エラー時の出力)を参照してください。
