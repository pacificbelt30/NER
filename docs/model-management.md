# モデル取得と接続設定

[ドキュメントの目次](README.md) · [ローカル翻訳](local-translation.md) · [API仕様](api.md)

## Web UIから使う

「設定」→「翻訳方式の準備」で各方式を開きます。

- 「接続・モデル設定」からIndexの接続先・モデル名・任意のBearer APIキー、Googleの接続先、TexTraの認証情報、Bergamotのファイル配置を保存できます。
- 「この方式を既定にする」で、次回の既定の翻訳方式にできます。変更は次に開始する翻訳から適用し、実行中の翻訳は受付時の設定を使います。
- 秘密情報は保存済みかだけを表示します。入力を空欄にして保存すると以前の値を維持します。Indexのキーを削除する場合は専用チェックボックスを使います。
- 別の画面や設定ファイルで更新されていた場合は上書きを防ぎます。必要な入力を控えて「保存済み設定を読み直す」を押してください。

Indexの欄の「モデルをダウンロード」で形式を選び、「モデルを取得」を押すと、**NERを動かすサーバー**へ保存します。ブラウザーを閉じても取得は継続し、設定画面を開き直すと状況を表示します。進捗、検証中、完了、失敗理由を確認でき、停止と再試行にも対応します。

| 形式 | 想定する実行環境 | モデル本体の容量の目安 |
| --- | --- | --- |
| `gguf-q4`（既定） | llama.cpp、CPU向けの軽量なQ4_K_M | 約1.31 GB |
| `gguf-q8` | llama.cpp、Q8_0 | 約2.08 GB |
| `hf` | vLLMなど、標準Safetensors一式 | 約4.57 GB |

モデルは[IndexTeamの公式GGUF配布](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF)または[標準モデル配布](https://huggingface.co/IndexTeam/Index-Translate-2B)の固定リビジョンから取得します。LFS資産はSHA-256、それ以外はGitのblobハッシュとサイズで検証します。検証に通ったファイルだけを正式な名前へ置き換えます。未完了データは`.part`に保持し、再試行時にHTTP Rangeが利用できる場合は続きから取得します。サーバーがRangeに対応しない場合は最初から取り直します。

ダウンロードの完了は、モデルサーバーの起動完了ではありません。画面に保存先と起動例を表示します。[実行環境の準備](local-translation.md)を行い、llama.cppやvLLMを起動してから、そのAPI接続先を同じ画面に保存してください。待受IP・公開ポートはモデルサーバーの配置に合わせて指定します。別ホストや別コンテナのモデルサーバーで利用する場合は、ファイルの共有・転送が必要です。モデル取得だけでNERが外部のプロセスやコンテナを起動することはありません。

本文テキストを抽出して翻訳するため、GGUF版の画像用mmprojは取得対象に含めません。Bergamotのモデル取得は以下のCLIに対応し、Bergamotの実行ファイルのビルドは[ローカル翻訳](local-translation.md#firefox--bergamot)の手順を使います。

## CLIから使う

サーバーの起動やログインは不要です。

```sh
# Indexの軽量GGUFを取得
uv run --locked python launcher.py dl index

# 形式と保存先を指定
uv run --locked python launcher.py dl index --format gguf-q8 --output models/index/q8
uv run --locked python launcher.py dl index --format hf --output models/index/hf

# Bergamotの英日モデルを取得
uv run --locked python launcher.py dl bergamot

# API接続先と既定の翻訳方式を保存
uv run --locked python launcher.py config set index \
  --base-url http://model.example.com:8001/v1 \
  --model IndexTeam/Index-Translate-2B --default

# 保存済み設定を確認（秘密情報は表示しない）
uv run --locked python launcher.py config show index
uv run --locked python launcher.py config show
```

`download` は `dl` の別名です。`--output` を省略した場合、Indexは `<保存先>/models/index/<形式>/`、Bergamotは `<保存先>/models/bergamot/en-ja/` に保存します。取得は明示したコマンド・ボタン操作でだけ実行し、モデル本体をソースコードやDockerイメージに含めません。

IndexのAPIキーは `--api-key-stdin` で標準入力の1行から指定できます。複数の認証項目を渡す場合は、保護したJSONファイルを `--values-file` で指定します。`--values-file -` は標準入力のJSONオブジェクトを読みます。

```sh
uv run --locked python launcher.py config set index \
  --base-url https://model.example.com/v1 --api-key-stdin

uv run --locked python launcher.py config set textra --values-file /private/textra-settings.json
```

JSONの項目名はYAMLと同じです。例：`{"NAME":"自分のアカウント","KEY":"自分のキー","SECRET":"自分のシークレット","URL":"英日翻訳APIのURL"}`。現在の設定との差分を保存します。指定を省いた項目は維持し、Indexの `API_KEY` に空文字列を指定すると解除します。

`--config` と `--storage` は読み込むYAMLと共有保存先を指定します。環境変数の `NER_CONFIG_PATH`、`NER_STORAGE_PATH` でも指定できます。Web UI・CLIで同じ保存先を使うと設定を共有します。

```sh
uv run --locked python launcher.py --config /etc/ner/config.yml --storage /srv/ner-data config show
uv run --locked python launcher.py --storage /srv/ner-data dl index
uv run --locked python launcher.py --storage /srv/ner-data translate paper.pdf
```

## 永続化とDocker

画面・CLIで保存する接続設定は `<保存先>/backend-settings.json` に0600の権限で保存し、元のYAMLは変更しません。指定した項目だけをYAMLより優先して読み、他の方式・共通項目は維持します。YAMLが未作成でも画面から初回設定を保存できます。YAMLが存在して書式が壊れている場合は、元ファイルを修正してから再読み込みしてください。

優先順位は、明示したライブラリ引数 `config={...}`、保存した接続設定、YAMLの順です。CLIの `translate` も共有設定を読みます。`FONT` などの共通項目はYAMLで設定します。

Dockerでは `/data` の名前付きボリュームへ設定・モデル・取得履歴を保存します。`config.yml` は読み取り専用マウントのまま使えます。例：

```sh
docker compose run --rm ner python launcher.py dl index
docker compose run --rm ner python launcher.py config set index \
  --base-url http://index:8001/v1 --model IndexTeam/Index-Translate-2B
```

モデルを別コンテナで読む場合は同じモデル保存先を読み取り専用で共有し、推論側のユーザーに読み取り権限を付けます。NERのデータボリュームには認証情報もあるため、推論側には必要なモデルディレクトリだけを共有する構成が適しています。NERとモデルサーバー間の接続は[Docker運用](docker.md#翻訳モデルへの接続)を参照してください。

モデル取得はPDF処理キューとは別に実行し、同時取得は1件です。同じ形式の実行中リクエストは同じ取得ジョブを返します。CLIと画面が同じモデル保存先を同時に更新しないようファイルロックも使用します。サーバー停止で中断された取得は履歴に残り、再試行できます。取得履歴やモデルはPDF履歴の保存期間では削除しません。
