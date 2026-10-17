# Docker運用

[ドキュメントの目次](README.md) · [サーバとWeb UI](server.md)

## 初回起動

Docker EngineとComposeを用意し、リポジトリのルートで実行します。ホスト側にPythonやuvを入れる必要はありません。

```sh
cp config_sample.yml config.yml
# config.yml の翻訳方式・接続先を編集する
docker compose build
docker compose run --rm ner python -m ner.auth setup
docker compose up -d
docker compose ps
```

ブラウザの接続先は `http://<配置先ホストの名前またはIP>:8000/` です。同じPCで使う場合は `http://localhost:8000/` です。アカウントは対話入力で作成します。起動時のモデル取得や既定パスワードの設定は行いません。

標準イメージではGoogle・TexTra・HTTP接続のIndexを利用できます。Bergamotは後述の追加ビルドを使います。イメージ内の依存関係は `uv.lock` から本番用だけをインストールし、非rootユーザー（UID/GID 10001）・APIサーバ1ワーカー・自動リロードなしで起動します。コンテナのヘルスチェックはログイン不要のセッション状態APIを使います。

## 公開ポートとHTTPS

必要なら [.env.example](../.env.example) を `.env` にコピーして設定します。

```sh
cp .env.example .env
# 例: .env の NER_PORT=8080 に変更
docker compose up -d
```

この場合の接続先は `http://<配置先ホスト>:8080/` です。Composeの `NER_PORT` はホストの公開ポートを変え、コンテナ内は8000番を使います。`NER_BIND_ADDRESS` はホストの待受IPで、既定は全IPv4インターフェースの `0.0.0.0` です。アクセス可能なホスト名を表す設定ではありません。同じホストのリバースプロキシからだけ接続するなら `127.0.0.1` にします。

HTTPSで公開する場合はリバースプロキシを前段に置き、`.env` に `NER_COOKIE_SECURE=1` を設定します。公開先は専用ホスト名の `/` とし、元の `Host` と `X-Forwarded-Proto`・`X-Forwarded-For` を渡してください。`FORWARDED_ALLOW_IPS` には実際のプロキシのIP・ネットワークだけを指定します。UIは同じオリジンの相対URLを使い、公開ホスト名をビルド時に埋め込む必要はありません。

## データの保存と更新

名前付きボリューム `ner-data` の `/data` に、アカウント・セッション・保存期間設定・履歴・PDFを保存します。`config.yml` は読み取り専用でマウントし、存在しない場合は起動を失敗させます。モデル・認証情報・履歴・ホストの仮想環境はビルドコンテキストに含めません。

```sh
docker compose logs -f ner
docker compose restart ner
docker compose down
docker compose up -d --build
```

`down` では名前付きボリュームが残ります。`down --volumes` はログイン情報と履歴を含めて削除するため、保存する場合は付けないでください。バックアップはサーバを停止してボリューム全体と `config.yml` を保存します。既存データを移す場合はUID/GID 10001が読み書きできる所有権にしてください。

パスワードの変更には初回と同じ `docker compose run --rm ner python -m ner.auth setup` を使い、その後に `docker compose restart ner` を実行します。

## 翻訳モデルへの接続

コンテナ内の `localhost` はコンテナ自身です。`INDEX.BASE_URL` はモデルが動く場所に合わせて設定します。

| モデルの配置 | `BASE_URL` の例 |
| --- | --- |
| 同じDockerネットワークの `index` サービス | `http://index:8001/v1` |
| Dockerホスト | `http://host.docker.internal:8001/v1` |
| 別のホスト | `http://192.168.1.50:8001/v1` または `https://model.example.com/v1` |

Composeには `host.docker.internal:host-gateway` の名前解決を設定しています。ホストのモデルがループバックでしか待ち受けていない場合、コンテナからは接続できません。モデルをコンテナから到達できるインターフェースで待ち受けさせてください。内部モデル宛てに環境変数のHTTPプロキシを使いたくない場合は、Composeの `environment` に `NO_PROXY: localhost,127.0.0.1,::1,index,host.docker.internal` などを追加します。

IndexモデルサーバのGPU・CPU実行環境は別途用意します。NERイメージにはモデルの重みもvLLM/llama.cppも含めません。[Indexの設定](local-translation.md#index-translate-2b)を参照してください。

## Bergamot

追加イメージはMozillaの固定リビジョンから常駐ワーカーをコンパイルします。英日モデルの重みはイメージに含めず、ホストの `models/` を読み取り専用でマウントします。現在のビルド設定はx86-64向けです。

ホストにuvがある場合は次のコマンドでモデルを準備できます。

```sh
uv run --locked python scripts/download_bergamot_model.py
```

Python環境がないホストでは、標準イメージを使って取得できます。書き込み先は実行ユーザーが所有するディレクトリを使います。

```sh
mkdir -p models
docker compose build
docker compose run --rm --user "$(id -u):$(id -g)" \
  --volume "$PWD/models:/models" ner \
  python scripts/download_bergamot_model.py --output /models/bergamot/en-ja
```

モデルファイルはコンテナのUID 10001から読み取れる必要があります。取得スクリプトは公開モデルのファイルを0644で保存します。以前のスクリプトで取得したモデルや、読み取りを制限するumaskで作成したディレクトリを使う場合は、モデルディレクトリだけに読み取り・ディレクトリ通過権限を付けてください。NERのアカウント情報やAPI認証情報を含むファイルとは別です。

```sh
chmod -R a+rX models/bergamot/en-ja
```

NERの `config.yml` を次のように設定します。

```yaml
PRIORITY: BERGAMOT
BERGAMOT:
  WORKER_BINARY: /usr/local/bin/ner-bergamot-worker
  MODEL_CONFIG: /models/bergamot/en-ja/config.yml
  NUM_WORKERS: 1
  TIMEOUT: 300
FONT: ''
```

```sh
docker compose -f compose.yaml -f compose.bergamot.yaml build
docker compose -f compose.yaml -f compose.bergamot.yaml run --rm ner python -m ner.auth setup
docker compose -f compose.yaml -f compose.bergamot.yaml up -d
```

更新・再起動時も同じ2つの `-f` を指定します。初回ビルドにはコンパイラと上流依存ソースの取得が必要で、標準イメージより時間とメモリを使います。モデル準備後の翻訳処理は外部サービスへ送信しません。

追加イメージにはビルドしたエンジンの上流ソースと通知を `/usr/local/src/firefox-translations`、NERのネイティブワーカーのソースを `/usr/local/src/ner-native` に含めます。配布時はNERのMITだけでなく、PyMuPDFやエンジンなど同梱依存のライセンス条件も確認してください。モデルを別途再配布する場合のライセンスは、そのモデルの配布元の条件に従います。

## ビルド時に独自CAが必要な環境

Dockerfileは任意のBuildKitシークレット `proxy_ca` に対応します。通常は不要です。HTTPSプロキシなどで独自CAが必要なら、システムの証明書と独自CAを含むPEMバンドルを渡します。証明書はイメージへ保存しません。

```sh
docker build --secret id=proxy_ca,src=/path/to/combined-ca.pem \
  --target runtime -t ner:local .
```

Dockerデーモン・ビルドのプロキシ設定は環境に合わせて別途設定します。実行時の翻訳先にも独自CAが必要な場合は、CAバンドルを読み取り専用でマウントし、Requestsの `REQUESTS_CA_BUNDLE` を指定してください。

uvのレイヤーキャッシュと同期の仕様は[uvのDockerガイド](https://docs.astral.sh/uv/guides/integration/docker/)、サービス名の名前解決は[Composeのネットワーク仕様](https://docs.docker.com/compose/how-tos/networking/)を参照してください。
