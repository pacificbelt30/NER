# ローカル翻訳

[ドキュメントの目次](README.md) · [翻訳設定](configuration.md)

Index-Translate-2BとFirefox / Bergamotを使用できます。英語から日本語への翻訳が対象です。ローカル方式の利用にTexTraの認証情報やNLTKのトークナイザデータは不要です。

以下はリポジトリで使用してきた構成例です。Indexの実モデルによる推論は受け入れ試験の対象にしていません。Bergamotは記載の固定リビジョンと英日モデルで検証しています。検証範囲は[性能・レイアウト評価](benchmarks.md)を参照してください。

## Index-Translate-2B

NERとは別のプロセスで、[IndexTeam/Index-Translate-2B](https://huggingface.co/IndexTeam/Index-Translate-2B)を提供するChat Completions互換サーバを起動します。モデルサーバの依存関係はNERのPython環境に追加する必要はありません。

### モデルサーバの起動例

GPU環境では、Qwen3.5に対応する実行環境が必要です。別のCUDA環境でのvLLM起動例です。

```sh
pip install -U vllm
vllm serve IndexTeam/Index-Translate-2B \
  --served-model-name IndexTeam/Index-Translate-2B \
  --host 127.0.0.1 --port 8001 --max-model-len 32768
```

CPU向けには、[公式GGUFリポジトリ](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF)の `Index-Translate-2B.Q4_K_M.gguf` を `models/index/` に置き、対応するllama.cppのサーバを起動する構成もあります。

```sh
llama-server -m models/index/Index-Translate-2B.Q4_K_M.gguf \
  --alias IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8001 \
  -ngl 0 -c 8192 --jinja --chat-template-kwargs '{"enable_thinking":false}'
```

これらは起動例であり、NERのテストによる実モデルの動作保証ではありません。実行環境・ハードウェアの要件は[Indexの公式配信手順](https://github.com/bilibili/Index-Translate/tree/main/inference/llm)を確認してください。PDFの本文テキストを扱うため、この経路で画像用のプロジェクタは使用しません。

### NERの設定

```yaml
PRIORITY: 'INDEX'
INDEX:
  BASE_URL: 'http://127.0.0.1:8001/v1'
  MODEL: 'IndexTeam/Index-Translate-2B'
  MAX_TOKENS: 4096
  TIMEOUT: 300
  CHUNK_CHARS: 2000
FONT: ''
```

- `MODEL` はモデルサーバが公開している名前に合わせます。
- `MAX_TOKENS` は各リクエストの出力上限です。途中で切れた応答や空の応答は訳文として採用しません。
- `BASE_URL` はHTTP(S)のURLです。同じホストの例のほか、`http://index:8001/v1` のようなDockerサービス名や `https://model.example.com/v1` のようなリモートホストも指定できます。認証情報を埋め込んだURL・クエリ・フラグメントは指定できません。
- `127.0.0.1`・`localhost`・`::1` への接続はHTTPプロキシを使いません。それ以外はRequestsの `HTTP_PROXY`・`HTTPS_PROXY`・`NO_PROXY` に従います。内部モデルサーバを直接呼ぶ場合は、その名前を `NO_PROXY` に含めます。
- 通常のAPIサーバが使う8000番と重複しないよう、モデルサーバの例では8001番を使います。

NERは `BASE_URL` に `/chat/completions` を付けて接続し、翻訳指示、保護トークン、ブロックの役割と見出し文脈を送ります。thinkingは無効化します。オフラインで使う場合は、モデルの重みを事前に取得してください。

上のモデル起動例はNERと同じホスト上で直接実行する場合です。別ホストや別コンテナから接続する場合は、モデル側も到達可能なインターフェースで待ち受ける必要があります。NERをDockerで起動した場合のlocalhostはNERコンテナ自身です。接続先の選び方は[Docker運用](docker.md#翻訳モデルへの接続)を参照してください。

## Firefox / Bergamot

[Mozillaの翻訳エンジン](https://github.com/mozilla/translations/tree/main/inference)のネイティブ実行ファイルと、ダウンロード済みモデルを使います。Firefox自体の起動や、旧PyPIの `bergamot` パッケージは不要です。

### ネイティブ実行ファイルのビルド

Git、CMake 3.20以上、C++17コンパイラを用意します。以下は検証済みの固定リビジョンを使う例です。

```sh
git clone https://github.com/mozilla/translations.git firefox-translations
git -C firefox-translations checkout 69455acaecbe8650cdba988dbcf7c10ca20e7c48
cmake -S firefox-translations/inference -B firefox-translations/inference/build \
  -DCMAKE_BUILD_TYPE=Release -DUSE_WASM_COMPATIBLE_SOURCE=ON \
  -DCOMPILE_CPU=ON -DCOMPILE_CUDA=OFF -DUSE_INTGEMM=ON -DSSPLIT_USE_INTERNAL_PCRE2=ON \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build firefox-translations/inference/build --target translator-cli -j 2
```

初回ビルドでは上流の依存ソースを取得します。コマンドはNERのリポジトリを作業ディレクトリとして実行します。

### 英日モデルの取得

```sh
uv run --locked python scripts/download_bergamot_model.py
```

MozillaのレジストリからReleaseの英日モデル、語彙、語彙ショートリストを取得・展開し、公開されているサイズとSHA-256を照合します。既定の出力先は `models/bergamot/en-ja/` です。モデル用の `config.yml` も生成します。

ダウンロードはこの明示的な準備時にだけ行います。モデルファイルはGitの管理対象外です。

### NERの設定

```yaml
PRIORITY: 'BERGAMOT'
BERGAMOT:
  BINARY: './firefox-translations/inference/build/src/app/translator-cli'
  MODEL_CONFIG: './models/bergamot/en-ja/config.yml'
  NUM_WORKERS: 1
  TIMEOUT: 300
FONT: ''
```

NERの `config.yml` と、`MODEL_CONFIG` が指すモデル用 `config.yml` は別のファイルです。`BINARY` がPATH上にある場合は `translator-cli` だけでも指定できます。モデルや実行ファイルの相対パスは作業ディレクトリが基準で、`~` の展開にも対応しています。

`NUM_WORKERS` はエンジンのCPUスレッド数、`TIMEOUT` は1回の呼び出しのタイムアウト秒数です。この構成では未キャッシュの翻訳リクエストごとにエンジンを起動します。長いブロックを複数チャンクに分けた場合も、それぞれ別の呼び出しになります。

### モデルを保持する常駐ワーカー

文書の処理中にモデルを読み込んだままにするには、NERが提供するワーカーを同じMozillaビルドに追加します。

```sh
cmake -S firefox-translations/inference -B firefox-translations/inference/build \
  -DCMAKE_PROJECT_INCLUDE="$PWD/native/bergamot_worker.cmake"
cmake --build firefox-translations/inference/build --target ner-bergamot-worker -j 2
```

NERの `BERGAMOT` セクションへ次を追加します。

```yaml
  WORKER_BINARY: './firefox-translations/inference/build/ner-bergamot-worker'
```

`WORKER_BINARY` は `BINARY` より優先されます。最初の非空ブロックで起動し、長さを付けたUTF-8プロトコルでブロック境界を保持し、文書の処理後に終了します。既存のエンジンライブラリを再利用してビルドします。

タイムアウトやワーカー異常時はプロセスを終了します。失敗した箇所の扱いは `ERROR_POLICY` に従います。既定では原文と説明を残したPDFを作り、オンライン方式へ切り替えません。

## 翻訳を実行する

エンジンと設定が準備できたら、[READMEのCLI手順](../README.md#コマンドラインで使う)または[Web UI](server.md)から実行します。
