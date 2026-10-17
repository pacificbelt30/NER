# NER

英語のPDFを、元のページ構成をできるだけ保ちながら日本語に翻訳するツールです。ブラウザ、API、コマンドラインから利用できます。

## できること

- Google翻訳・TexTra・ローカルのIndex-Translate-2B・Firefox / Bergamotを選択して翻訳。
- 原文と訳文のプレビュー、原文と訳文を交互に並べた比較用PDFの作成。
- 翻訳履歴の検索、結果の再表示、PDFの再ダウンロード。保存期間は既定で無期限。
- 1アカウントのログインと、画面からの保存期間設定。
- Web UIから翻訳APIの接続先・認証情報を保存し、Indexのモデルをダウンロード。
- 一部の翻訳や配置に失敗しても、原文を残した「注意付きPDF」を出力。
- 注意箇所を後から確認し、原文・候補の採用や手修正を選んで修正版PDFを作成。

対象は文字を選択できる英語PDFです。スキャン画像には事前の文字認識（OCR）が必要です。複雑な表や段組みなど、元のレイアウトを完全に再現できない文書もあります。

## はじめる

Python 3.11と[uv](https://docs.astral.sh/uv/getting-started/installation/)を用意し、リポジトリのルートで実行します。

### 1. インストールと翻訳設定

```sh
uv sync --locked
cp config_sample.yml config.yml
```

`config.yml` の `PRIORITY` と、利用する翻訳方式の設定を編集してください。

| 設定値 | 翻訳方式 | 準備するもの |
| --- | --- | --- |
| `GOOGLE` | Google翻訳へのHTTP接続 | ネットワーク接続 |
| `TEXTRA` | みんなの自動翻訳＠TexTra | アカウント・API認証情報 |
| `INDEX` | Index-Translate-2B | 別途起動するローカルモデルサーバ |
| `BERGAMOT` | Firefox / Bergamot | ネイティブ実行ファイルと英日モデル |

サンプルの既定は `GOOGLE` です。TexTraのサンプル値は実際の認証情報に置き換えてください。設定項目は[翻訳設定](docs/configuration.md)、ローカルエンジンの準備は[ローカル翻訳](docs/local-translation.md)を参照してください。

### 2. ログインアカウントの作成と起動

```sh
uv run --locked python -m ner.auth setup
uv run --locked python api_server.py
```

初回設定ではユーザー名とパスワードを対話形式で入力します。ブラウザで `http://<配置先ホストの名前またはIP>:8000/` を開き、作成したアカウントでログインしてください。同じPCで使う場合は `http://localhost:8000/` です。待受アドレス・ポートは `--host`・`--port` で変更できます。

「新しく翻訳」でPDFを選択し、翻訳方式を選んで開始します。完了したPDFはダウンロードでき、「翻訳履歴」からも開き直せます。「設定」では、今後完了する翻訳の保存期間を変更できます。

ログイン・保存先・サーバ運用の詳細は[サーバとWeb UI](docs/server.md)、APIの利用方法は[API仕様](docs/api.md)を参照してください。

## Dockerで使う

Docker EngineとComposeを用意し、`config.yml` を作成・編集してから実行します。

```sh
docker compose build
docker compose run --rm ner python -m ner.auth setup
docker compose up -d
```

接続先は `http://<配置先ホストの名前またはIP>:8000/` です。履歴・PDF・ログイン情報は名前付きボリュームへ保存します。公開ポートの変更、リモートモデルへの接続、Bergamot用イメージは[Docker運用](docs/docker.md)を参照してください。

## コマンドラインで使う

CLIにはサーバの起動やログインは不要です。翻訳には `config.yml` と、画面・CLIで保存した共有設定を使います。

```sh
uv run --locked python launcher.py info paper.pdf       # ページ数・翻訳対象ブロック数の確認
uv run --locked python launcher.py translate paper.pdf  # paper_new.pdf を作成
uv run --locked python launcher.py combine paper.pdf    # paper_all.pdf を作成
uv run --locked python launcher.py help
```

モデル取得と接続設定もCLIから実行できます。

```sh
uv run --locked python launcher.py dl index                 # Index GGUF Q4_K_M
uv run --locked python launcher.py dl index --format hf     # 標準モデル一式
uv run --locked python launcher.py dl bergamot              # Bergamot英日モデル
uv run --locked python launcher.py config set index --base-url http://model.example.com:8001/v1
uv run --locked python launcher.py config show index        # 秘密情報は非表示
```

Web UIとCLIは同じ保存先の設定を共有します。取得したIndexを使うには、別途モデルサーバーを起動して接続先を設定します。形式、保存先、APIキー、Dockerの使い方は[モデル取得と接続設定](docs/model-management.md)を参照してください。

出力先は入力PDFと同じディレクトリです。`combine` は作成済みの `paper_new.pdf` と原文を組み合わせます。`info` と `combine` に翻訳エンジンの設定は不要です。

翻訳や配置の一部に失敗した場合は原文を保持し、PDFの注釈と末尾のレポートで内容を知らせます。配置できなかった有効な訳文もレポートに掲載します。入力を開けない場合や保存できない場合は、PDFを出力できません。詳しくは[PDF処理とエラー方針](docs/pdf-layout.md)を参照してください。

## 詳しいドキュメント

| 読みたい内容 | ドキュメント |
| --- | --- |
| ドキュメント全体の案内 | [docsの目次](docs/README.md) |
| 翻訳エンジン、フォント、再試行などの設定 | [翻訳設定](docs/configuration.md) |
| Index・Bergamotの導入とモデルの準備 | [ローカル翻訳](docs/local-translation.md) |
| 画面・CLIからモデルを取得し、API設定を保存 | [モデル取得と接続設定](docs/model-management.md) |
| ログイン、履歴、保存期間、サーバ運用 | [サーバとWeb UI](docs/server.md) |
| 注意箇所の採用判断、URL・本文の手修正 | [注意箇所の確認と手修正](docs/human-review.md) |
| Docker・Compose、永続化、リモート配置 | [Docker運用](docs/docker.md) |
| 認証、ジョブ、ダウンロード、旧API | [API仕様](docs/api.md) |
| 翻訳対象の判定、表、レイアウト、エラー時の出力 | [PDF処理とエラー方針](docs/pdf-layout.md) |
| ソース構成、テスト、検証コマンド | [開発と検証](docs/development.md) |
| PDFライブラリ比較、測定条件、残る改善点 | [性能・レイアウト評価](docs/benchmarks.md) |
