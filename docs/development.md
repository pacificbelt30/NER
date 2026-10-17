# 開発と検証

[ドキュメントの目次](README.md) · [性能・レイアウト評価](benchmarks.md)

## 開発環境

Python 3.11を使います。

```sh
uv sync --locked
uv run --locked python -m pytest test -q
```

通常のテストではオンライン翻訳の認証情報や実モデルは不要です。実モデルの受け入れ試験は別に実行します。

依存定義は [pyproject.toml](../pyproject.toml)、固定した解決結果は [uv.lock](../uv.lock) で管理します。`uv sync --locked` はロックと定義の不一致を検出します。依存変更時は `uv add` / `uv remove` または定義の編集後に `uv lock` を使い、ロックファイルもコミットしてください。実行時だけの環境は `uv sync --locked --no-dev`、通常の開発環境には `dev` グループが入ります。`browser`・`benchmark` は明示した場合だけ追加されます。既存の仮想環境も `uv sync --locked` で同期できます。

PDF処理はNLTKのトークナイザデータを必要としません。`PaperExtract` のテキスト抽出ヘルパーを `is_split=True` で呼ぶ場合だけ、次のデータを準備します。

```sh
uv run --locked python -m nltk.downloader punkt punkt_tab
```

## ソース構成

| ファイル | 役割 |
| --- | --- |
| [launcher.py](../launcher.py) | CLIの `translate`・`combine`・`info`・`help` |
| [api_server.py](../api_server.py) | ログイン、アップロード、ジョブ、ダウンロード、Web UI配信 |
| [ner/auth.py](../ner/auth.py) | 1アカウント認証、セッションの継続・失効、初回設定 |
| [ner/jobs.py](../ner/jobs.py) | 単一キュー、履歴・保存期間、再起動後の復元 |
| [ner/api_models.py](../ner/api_models.py) | APIの入力・応答モデル |
| [ner/textra_api.py](../ner/textra_api.py) | PDF翻訳全体の流れ。互換性のため従来のクラス名を保持 |
| [ner/translation.py](../ner/translation.py) | 翻訳方式の選択、再試行、文書内キャッシュ |
| [ner/http_translators.py](../ner/http_translators.py) | Google・TexTraへのHTTP接続 |
| [ner/local_translators.py](../ner/local_translators.py) | IndexのHTTP接続、Bergamotの起動・常駐通信 |
| [ner/text_blocks.py](../ner/text_blocks.py) | 文字列保護、チャンク分割、検証・復元 |
| [ner/translation_policy.py](../ner/translation_policy.py) | 自動翻訳対象の判定 |
| [ner/paper_extract.py](../ner/paper_extract.py) | PDF抽出、翻訳対象の除去、配置・保存 |
| [ner/pdf_tables.py](../ner/pdf_tables.py) | 表の検出・セルへの分離 |
| [ner/block_layout.py](../ner/block_layout.py) | 共通HTML配置とサイズ調整 |
| [ner/layout_space.py](../ner/layout_space.py) | 段組み・障害物を考慮した領域拡張 |
| [ner/pdf_warnings.py](../ner/pdf_warnings.py) | 注釈、エラーレポート、JSON添付 |
| [ner/combine_pdfs.py](../ner/combine_pdfs.py) | 原文・訳文・追加レポートの比較PDF化 |
| [native/](../native/) | 常駐BergamotワーカーとCMakeの追加ターゲット |

フロントエンドは外部CDNやビルドを必要としないHTML・CSS・JavaScriptです。

| ファイル | 役割 |
| --- | --- |
| [web/app.js](../web/app.js) | 画面切り替え、アップロード、ジョブの追跡 |
| [web/api.js](../web/api.js) | API呼び出しと共通処理 |
| [web/auth.js](../web/auth.js)、[web/login.js](../web/login.js) | 認証確認、ログイン・ログアウト |
| [web/job-view.js](../web/job-view.js) | 状態・進捗の表示 |
| [web/preview.js](../web/preview.js) | ページ表示、拡大、古い非同期応答の無効化 |
| [web/history.js](../web/history.js) | 履歴検索、絞り込み、一覧・取得 |
| [web/settings.js](../web/settings.js) | 保存期間設定 |

動的なファイル名・エラーメッセージはHTMLとして挿入せず、テキストとして表示します。認証トークンをlocalStorageへ保存する実装ではありません。

## Pythonライブラリからの利用

リソースを閉じるため、コンテキストマネージャを使います。

```python
from ner import TextraConnection

with TextraConnection('paper.pdf') as job:
    job.get_pdf_info()
    job.post_page()
    output_path = job.insert()
    print(output_path, job.result_summary)
```

明示的な設定は `config={...}`、エラー方針は `error_policy='strict'` などで渡せます。サーバのログイン機能はこのライブラリ利用には適用しません。

## 実モデルとブラウザの受け入れ試験

[ローカル翻訳](local-translation.md)に従ってBergamotワーカーとモデルを用意します。PlaywrightとChromiumも別途必要です。

```sh
uv sync --locked --group browser
uv run --locked --group browser python -m playwright install chromium
uv run --locked --group browser python scripts/verify_end_to_end.py \
  --worker /path/to/ner-bergamot-worker \
  --model /path/to/model/config.yml \
  --output pdf/end-to-end
```

このスクリプトは一時ディレクトリに設定・アカウント・アップロード先を作り、実際のAPIサーバとブラウザを起動し、終了時に片付けます。通常翻訳、注意付きPDF、ログイン、未ログイン時の取得拒否、履歴検索・再取得、保存設定、中止・再試行、再起動後の復元、モバイル表示を確認します。

[scripts/smoke_web_ui.cjs](../scripts/smoke_web_ui.cjs)はログイン導入前の補助スクリプトで、現在のログイン画面を操作する処理がありません。現在の通し確認には上記の `verify_end_to_end.py` を使用してください。

## PDF構造と保護文字列の検証

モデルなしの構造復元実験です。

```sh
uv run --locked python scripts/audit_pdf_layout.py --output pdf/layout-audit
```

段組み、見出し、URL、コード、リスト、表、色付き背景、回転文字を含む2ページの検証PDFと、レポート・比較PDF・PNGを生成します。現在の実装を入力と同じ文で評価する経路と、人手で領域・役割・日本語訳を与えた実験用配置を区別しています。後者は自動文書理解の精度を示すものではありません。

実モデルを加える場合は次の指定を使います。

```sh
uv run --locked python scripts/audit_pdf_layout.py --output pdf/layout-audit/native \
  --bergamot-worker /path/to/ner-bergamot-worker \
  --bergamot-model /path/to/model/config.yml
uv run --locked python scripts/verify_block_translation.py --output pdf/block-audit \
  --worker /path/to/ner-bergamot-worker \
  --model /path/to/model/config.yml
```

`verify_block_translation.py` は保護文字列、コードの座標、クリック可能なリンクと、長いブロックの分割を確認します。

`audit_pdf_layout.py` の `--bergamot-html-worker` は、別ビルドした実験用HTMLワーカーの指定です。モデルのsoft alignmentを必要とする別の実験で、本番のBergamot経路では有効にしていません。

## 表とPDFライブラリの比較

```sh
uv run --locked python scripts/evaluate_pdf_tables.py --output pdf/table-audit/ruled
uv run --locked python scripts/evaluate_pdf_tables.py --output pdf/table-audit/borderless --borderless
uv run --locked python scripts/evaluate_pdf_tables.py --output pdf/table-audit/native \
  --worker /path/to/ner-bergamot-worker --model /path/to/model/config.yml
```

別の読み取り専用チェックアウトと比較する場合は `--code-root /path/to/baseline` を指定します。

PDFライブラリの比較には、本番では不要な追加パッケージを使います。

```sh
uv sync --locked --group benchmark
uv run --locked --group benchmark python scripts/benchmark_pdf_libraries.py \
  --output pdf/library-benchmark --repeat 7
```

比較条件、採用判断、数値の解釈は[性能・レイアウト評価](benchmarks.md)を参照してください。

## Dockerの通し確認

イメージをビルドしてから、隔離したコンテナ・ネットワーク・ボリュームで確認します。既存のアカウント・設定・PDF履歴は使いません。標準では別コンテナのIndex互換スタブを使い、モデルの翻訳品質は評価しません。

```sh
docker build --target runtime -t ner:local .
uv run --locked python scripts/verify_docker.py --image ner:local --output pdf/docker-index
```

初回アカウント作成、非root起動、別コンテナへの翻訳接続、公開ホスト名とHTTPS転送ヘッダー、Origin検証、PDFとプレビューの取得、コンテナ再作成後のセッション・履歴・保存設定・PDF保持、ヘルスチェックを検証します。作成したリソースは終了時に削除します。

Bergamotイメージと取得済みモデルでも同じ確認を行えます。

```sh
docker build --target bergamot -t ner:bergamot .
uv run --locked python scripts/verify_docker.py --image ner:bergamot \
  --model models/bergamot/en-ja/config.yml --output pdf/docker-bergamot
```

## 結果の扱い

[benchmarks/](../benchmarks/)のJSONは各変更時点の検証記録です。uv・Docker導入時の[検証記録](../benchmarks/deployment_results.json)には285テストと実モデル・ブラウザ・Dockerの確認を保存しています。過去の208・226・244・267件の記録は書き換えていません。現在のチェックアウトを検証する場合は上記のコマンドを実行してください。

検証PDF・PNGは `pdf/`、モデルは `models/`、サーバデータは `pdfstore/` に置く構成で、いずれもGit管理の対象外です。別プロジェクトの実装を参照する場合も、変更対象のリポジトリと検証用ファイルを区別してください。
