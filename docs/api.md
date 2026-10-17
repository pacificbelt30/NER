# API仕様

[ドキュメントの目次](README.md) · [サーバとWeb UI](server.md)

接続先は配置先のURLです。直接公開する場合は `http://<配置先ホストの名前またはIP>:8000`、HTTPSプロキシを使う場合は `https://ner.example.com` などを使います。同じPCで使う場合のみ `http://localhost:8000` です。起動中のサーバの `/docs` と `/openapi.json` で、実装から生成する仕様を確認できます。API仕様の閲覧もログインが必要です。

## 認証

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/auth/session` | 公開。アカウント設定済みか、ログイン済みかを返す |
| POST | `/api/auth/login` | 公開。フォームの `username`・`password` でログイン |
| POST | `/api/auth/logout` | 現在のセッションを失効させ、Cookieを削除 |

ログイン成功時はセッションCookieを設定し、JSONに `access_token`・`token_type: bearer`・`username` を返します。後続の呼び出しではCookieを引き継ぐか、`Authorization: Bearer <token>` を付けます。

未ログインはHTTP 401・`LOGIN_REQUIRED`、認証アカウントが未設定の場合はHTTP 503・`AUTH_NOT_CONFIGURED` です。ユーザー名・パスワードの不一致はHTTP 401・`INVALID_CREDENTIALS`、試行回数の制限はHTTP 429・`LOGIN_RATE_LIMITED` と `Retry-After: 60` です。

変更操作はNERと同じオリジンのブラウザから実行します。異なる `Origin` や `Sec-Fetch-Site: cross-site` を付けた変更操作はHTTP 403・`ORIGIN_REJECTED` になります。Originヘッダーを送らない通常のサーバ間クライアントは、CookieまたはBearerトークンで利用できます。

## ジョブAPI

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/capabilities` | 言語、50 MiBのアップロード上限、設定済みエンジン、保存期間 |
| GET | `/api/jobs` | 新しい順の履歴一覧 |
| POST | `/api/jobs` | PDFを受け付け、HTTP 202でジョブを返す |
| GET | `/api/jobs/{id}` | 状態・進捗・エラー・注意事項・ダウンロード先 |
| GET | `/api/jobs/by-key/{key}` | 受付キーからジョブを取得 |
| POST | `/api/jobs/{id}/cancel` | 待機・実行中のジョブを中止 |
| POST | `/api/jobs/{id}/retry` | 失敗・停止したジョブを、新しいジョブとして再試行 |
| GET | `/api/jobs/{id}/review` | 注意箇所の原文・候補・保存済みの採用方針を取得 |
| PUT | `/api/jobs/{id}/review` | 採用方針・修正文の下書きを保存 |
| POST | `/api/jobs/{id}/review` | 下書きから修正版PDFを作成 |
| POST | `/api/jobs/{id}/review/prepare` | 編集データのない以前の履歴を原本から再翻訳 |
| GET | `/api/jobs/{id}/files/{kind}` | `original`・`translated`・`comparison` のPDFを取得 |
| GET | `/api/jobs/{id}/preview/{kind}?page=1` | `original`・`translated` のページをPNGで取得 |
| GET | `/api/settings` | 保存期間設定を取得 |
| PUT | `/api/settings` | 保存期間設定を変更 |

`GET /api/capabilities` は `Cache-Control: no-store` を付け、サーバー設定をその都度確認します。`configuration.loaded` で設定ファイルを読めたか確認でき、読めなかった場合も全方式の準備先を返します。各 `backends` の `setup` は次の診断情報です。

| 項目 | 内容 |
| --- | --- |
| `code` | `CONFIGURED`、`MISSING_CONFIGURATION`、`CONFIGURATION_UNREADABLE`、`INVALID_SECTION`、`INVALID_FIELD`、`MODEL_CONFIG_NOT_FOUND`、`EXECUTABLE_NOT_FOUND`、`SAMPLE_CREDENTIALS`、`INVALID_CONFIGURATION` |
| `message` | 状態と必要な対応の説明。生の例外や設定値は含めない |
| `fields` | 確認する設定項目名。例：`INDEX.BASE_URL` |
| `url` | Web UIの準備画面へのリンク。例：`#settings/INDEX` |
| `verification` | `configuration_only`。稼働・認証・推論は未確認 |

既存の `configured` は、設定ファイルと構成の確認を通った場合に `true` です。ネットワークへの疎通確認や推論を実行したことは意味しません。TexTraの既知のサンプル認証情報は `SAMPLE_CREDENTIALS` として選択できず、ジョブを直接送信した場合も `BACKEND_NOT_CONFIGURED` を返します。APIキー・秘密情報・接続先の設定値・サーバー内のパスは診断応答に含めません。

### 翻訳の受付

`POST /api/jobs` はmultipartフォームを使います。

| 項目 | 必須 | 内容 |
| --- | --- | --- |
| `file` | はい | PDFファイル。上限50 MiB |
| `backend` | いいえ | `INDEX` / `BERGAMOT` / `GOOGLE` / `TEXTRA`。省略時はサーバ設定 |
| `output` | いいえ | `comparison`（既定）または `translated` |

成功時は `Location: /api/jobs/{id}` と `Retry-After: 1` を返します。完了までおおむね1秒ごとに状態を確認してください。

アップロード単位で任意の `Idempotency-Key` ヘッダーを指定できます。英数字・`_`・`-` の64文字以内です。同じキーの再送は既存のジョブを返します。ファイル名・翻訳方式・出力指定を変えた再送はHTTP 409・`IDEMPOTENCY_CONFLICT` になります。

受付キーは応答を受け取れなかった際の重複防止に使います。ファイル内容のハッシュによる重複判定ではありません。別の翻訳には新しいキーを使ってください。

### 状態と進捗

| `status` | 内容 |
| --- | --- |
| `queued` | 順番待ち |
| `running` | 処理中 |
| `cancelling` | 停止を要求済み |
| `succeeded` | PDFの保存が完了。注意付きPDFも含む |
| `failed` | 完了できなかった |
| `cancelled` | 中止済み |

`progress` には `stage`・`completed_blocks`・`total_blocks`・`page`・`pages` を含みます。チャンク分割時は `block_id`・`completed_chunks`・`total_chunks` も返します。

`warnings` は安全なエラーコード、説明、原文のページ・ブロック位置です。バックエンドの内部例外や認証情報は含めません。完了時の `result_summary` は次の内容です。

| 項目 | 内容 |
| --- | --- |
| `outcome` | `complete` / `partial` / `untranslated` / `complete_with_warnings` |
| `total_blocks` | 翻訳対象ブロック数 |
| `translated_blocks` | 訳文を配置できたブロック数 |
| `retained_blocks` | 原文を保持したブロック数 |
| `warning_count` | 注意事項の数 |
| `source_pages` | 原文のページ数 |
| `report_pages` | PDF末尾に追加したレポートのページ数 |

`partial` は一部のブロックだけ翻訳・配置できた状態、`untranslated` は翻訳対象を配置できず原文と説明を残した状態、`complete_with_warnings` はフォント代替などの注意を伴う完了です。詳細は[エラー時の出力](pdf-layout.md#エラー時の出力)を参照してください。

`downloads` は完成したPDFへのURLです。比較用PDFだけ作成に失敗した場合は、そのリンクを含めず、翻訳PDFの取得先と注意事項を返します。

### 履歴の検索

`GET /api/jobs` のクエリは以下のとおりです。

| 項目 | 既定値 | 内容 |
| --- | --- | --- |
| `q` | 空文字 | ファイル名の部分一致。大文字・小文字を区別せず、200文字以内 |
| `status` | 指定なし | 上記 `status` のどれか1つに一致する履歴 |
| `limit` | `20` | 1〜100件 |
| `offset` | `0` | 0以上 |

応答は `items`・`total`・`limit`・`offset` です。期限切れのジョブは一覧から除かれます。

### 再試行・PDF取得・保存期間

再試行できるのは `failed`・`cancelled` のジョブです。`backend`・`output` を任意のフォーム項目で変更できます。受付時に新しいIDを返し、元の履歴は残します。

ファイル取得に `?inline=true` を付けるとブラウザ表示用の応答になります。プレビューの `page` は1以上で、存在しないページはHTTP 404です。

保存期間は次のJSONで設定します。

```json
{"retention_days": null}
```

`null` は無期限、整数の1〜3650は完了後の日数です。変更は今後完了するジョブに適用し、既存の期限を変更しません。ジョブの `expires_at: null` も期限なしを表します。`/api/capabilities` の `result_retention_seconds` は無期限の場合 `null` です。

### 注意箇所の確認・修正

完了済みジョブに対して利用できます。`GET /api/jobs/{id}/review` は `available`、`revision`、`items` を返します。各項目の `source`・`candidate`・`candidate_complete`・`candidate_validated`・`can_adopt`・`literals` で原文と候補を比較できます。`editable: false` の文書全体・ページ全体の注意は、本文修正では解消できません。

`PUT` には取得した `revision` と、ブロックごとの判断を渡します。

```json
{"revision": 0, "decisions": [{"id": "取得した項目ID", "action": "edit", "text": "参照先 https://example.org/fixed を確認してください。"}]}
```

`action` は `pending`・`original`・`adopt`・`edit` です。`text` は `edit` のときだけ指定し、空欄や内部の保護記号を含まない2万文字以内の本文にします。`decisions` は下書き全体の置換です。指定を省いたブロックは未確認になります。保存すると `revision` が増えます。他の画面で先に保存された場合はHTTP 409・`REVIEW_CONFLICT` を返し、上書きしません。

保存後の `revision` と新しい受付キーを `POST` に渡します。

```json
{"revision": 1, "submission_key": "unique-review-key"}
```

HTTP 202で通常のジョブを返します。`kind: "review"`、`parent_id` が修正元の履歴IDです。同じキー・修正元・リビジョンの再送は同じジョブを返します。`kind: "translation"` は通常の翻訳です。編集用データの再作成にも `parent_id` を残します。元の履歴は変更せず、モデルを呼ばずに原本から再配置します。修正版にも訳文・比較用PDFがあります。

修正版の確認項目の `applied` は、その判断を最後のPDFに反映できた場合 `true`、配置失敗で原文を保持した場合 `false`、未確認または新たに下書きを変更した場合 `null` です。失敗した修正は通常の注意として残します。修正版の再試行もモデルを呼ばず、保存済みの修正内容を再配置します。

`available: false` の以前の履歴は `POST /review/prepare` で編集用データを作成できます。この操作は原本を再翻訳するためエンジンが必要です。元の翻訳方式を既定とし、任意のフォーム項目 `backend` で変更できます。[画面操作と保存内容](human-review.md)も参照してください。

## Pythonクライアントの例

`requests` を使い、対話入力でログインして翻訳・取得する例です。

```python
from getpass import getpass
from pathlib import Path
import time
import uuid

import requests

base = input('NERサーバURL（例: https://ner.example.com）: ').strip().rstrip('/')
with requests.Session() as client:
    response = client.post(base + '/api/auth/login', data={
        'username': input('ユーザー名: '),
        'password': getpass('パスワード: '),
    }, timeout=30)
    response.raise_for_status()

    with Path('paper.pdf').open('rb') as source:
        response = client.post(base + '/api/jobs',
            files={'file': ('paper.pdf', source, 'application/pdf')},
            data={'output': 'comparison'},
            headers={'Idempotency-Key': uuid.uuid4().hex}, timeout=120)
    response.raise_for_status()
    job_id = response.json()['id']
    deadline = time.monotonic() + 3600

    while True:
        response = client.get(base + f'/api/jobs/{job_id}', timeout=30)
        response.raise_for_status()
        job = response.json()
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f'待機を終了しました。受付済みジョブID: {job_id}')
        time.sleep(1)

    if job['status'] != 'succeeded':
        raise RuntimeError(job.get('error') or job['status'])
    print(job.get('result_summary'))  # 一部失敗の有無も確認する
    response = client.get(base + job['downloads']['translated'], timeout=120)
    response.raise_for_status()
    Path('paper_new.pdf').write_bytes(response.content)
```

この例の待機上限に達しても、サーバのジョブ自体は中止しません。IDを使って状態を取得できます。大きいPDFを扱うクライアントは、ダウンロードもストリームで保存してください。

## エラー応答

新しいAPIのエラーは次の形式です。

```json
{"error": {"code": "EMPTY_FILE", "message": "PDFが空です。別のファイルを選んでください。", "retryable": false}}
```

| HTTP | 主なケース |
| --- | --- |
| 400 | 空ファイルなど |
| 401 | 未ログイン、ログイン情報の不一致 |
| 403 | 別オリジンからの変更操作 |
| 404 | 不明なジョブ、存在しないページなど |
| 409 | 結果が未完成、再試行できない状態、受付キーの不一致 |
| 410 | 保存期限切れ、PDFファイルが取得できない |
| 413 | アップロード上限超過 |
| 415 | PDF以外の拡張子 |
| 422 | パラメータの検証エラー |
| 429 | 受付上限到達、ログイン試行制限 |
| 500 | アップロード先や設定への書き込み失敗など |
| 503 | 認証・翻訳方式・プレビューの準備ができない |

翻訳エンジンの失敗やPDF処理の失敗は、受付済みジョブの状態・注意事項にも表れます。`POST /api/jobs` のHTTP 202だけで、翻訳に成功したと判断しないでください。

## 旧API

従来クライアント向けのエンドポイントも残しています。ログインは必須です。

| メソッド | パス | 内容 |
| --- | --- | --- |
| POST | `/return_translated_file` | PDFを翻訳して返す |
| POST | `/return_origin_file` | 原文を返す |
| POST | `/return_translated_file_jaen` | 未実装。HTTP 501 |

いずれもファイルのフォーム項目は `file` です。旧APIは履歴を作らず、一時ファイルを応答後に削除します。従来形式の `detail` エラーを返す場合があり、翻訳・保存の致命的な失敗は502・500などになります。新規のクライアントではジョブAPIを使用してください。
