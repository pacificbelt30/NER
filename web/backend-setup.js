import { $ } from "./api.js";

const DOCS = "https://github.com/pacificbelt30/NER/blob/main/docs/";
const GUIDES = {
  INDEX: {
    label: "Index 2B", needs: "Indexモデルを動かすサーバーと、その接続先の設定が必要です。",
    steps: [
      "IndexTeam/Index-Translate-2Bのモデルを取得し、OpenAI Chat Completions互換のモデルサーバーを別途起動します。GPU・CPU環境に合うvLLMやllama.cppの導入手順は詳細ドキュメントを確認してください。NERのインストールだけではモデルサーバーは起動しません。",
      "NERを動かすサーバーから到達できるホスト名・IP・ポートを確認します。以下のモデルサーバーのホスト名を実際の配置先に置き換えてください。NERの8000番と重複しないポートを使います。",
      "INDEX.BASE_URLに /v1 までのURL、MODELにモデルサーバーが公開するモデル名を設定します。NERが /chat/completions を付けて接続します。",
    ],
    config: "INDEX:\n  BASE_URL: 'http://<モデルサーバーのホスト名またはIP>:8001/v1'\n  MODEL: 'IndexTeam/Index-Translate-2B'\n  MAX_TOKENS: 4096\n  TIMEOUT: 300\n  CHUNK_CHARS: 2000\n",
    docker: "Docker内のlocalhostはNERコンテナ自身です。同じネットワークのモデルなら http://index:8001/v1、Dockerホストなら http://host.docker.internal:8001/v1、別ホストならその名前・IPを指定します。モデル側もNERから接続できるアドレスで待ち受ける必要があります。",
    docs: "local-translation.md#index-translate-2b",
  },
  BERGAMOT: {
    label: "Firefox / Bergamot", needs: "翻訳エンジンの実行ファイルと、ダウンロード済みの英日モデルが必要です。",
    steps: [
      "Mozillaの翻訳エンジンをビルドし、translator-cliまたはNER用のner-bergamot-workerを配置します。Firefoxブラウザーを入れるだけでは利用できません。ビルド方法は詳細ドキュメントにあります。",
      "下のモデル取得コマンドを、NERを動かすサーバーのリポジトリ内で実行します。英日モデル・語彙・モデル用config.ymlを取得します。",
      "下の設定例ではtranslator-cliを使います。BINARYを実際の実行ファイルのパス、MODEL_CONFIGを取得したモデル用config.ymlのパスにします。常駐ワーカーを使う場合はWORKER_BINARYを設定します。NERの設定ファイルとモデル用config.ymlは別のファイルです。",
    ],
    command: "uv run --locked python scripts/download_bergamot_model.py",
    config: "BERGAMOT:\n  BINARY: './firefox-translations/inference/build/src/app/translator-cli'\n  MODEL_CONFIG: './models/bergamot/en-ja/config.yml'\n  NUM_WORKERS: 1\n  TIMEOUT: 300\n  CHUNK_CHARS: 700\n",
    docker: "標準イメージにはBergamotの実行ファイルがありません。compose.bergamot.yamlを追加してビルド・起動し、モデルを /models にマウントします。下のDocker用設定例を使い、モデルファイルにコンテナからの読み取り権限を付けます。",
    dockerConfig: "BERGAMOT:\n  WORKER_BINARY: '/usr/local/bin/ner-bergamot-worker'\n  MODEL_CONFIG: '/models/bergamot/en-ja/config.yml'\n  NUM_WORKERS: 1\n  TIMEOUT: 300\n  CHUNK_CHARS: 700\n",
    docs: "local-translation.md#firefox--bergamot",
  },
  GOOGLE: {
    label: "Google翻訳", needs: "Google翻訳へのネットワーク接続と、接続先URLの設定が必要です。",
    steps: [
      "NERを動かすサーバーからGoogle翻訳へHTTPS接続できることを確認します。翻訳するPDFの本文は接続先へ送信されます。",
      "GOOGLE.URLに下の接続先を設定します。この方式はGoogle Cloud Translationのサービスアカウント認証を使う方式ではありません。",
    ],
    config: "GOOGLE:\n  URL: 'https://translate.googleapis.com/translate_a/single'\n  TIMEOUT: 60\n  REQUEST_INTERVAL: 0\n  CHUNK_CHARS: 2000\n",
    docs: "configuration.md#google翻訳",
  },
  TEXTRA: {
    label: "TexTra", needs: "TexTraのアカウントとAPIキー・シークレット、英日翻訳APIの接続先が必要です。",
    steps: [
      "みんなの自動翻訳＠TexTraでアカウントを用意し、APIキー・APIシークレットと利用する英日翻訳APIのURLを確認します。",
      "NAME・KEY・SECRETを自分の認証情報に置き換えます。サンプルの値のままでは利用できません。URLは利用権限のある英日翻訳APIを指定してください。",
      "NERを動かすサーバーからAPIへHTTPS接続できることを確認します。翻訳するPDFの本文は接続先へ送信されます。",
    ],
    config: "TEXTRA:\n  NAME: 'YOUR_ACCOUNT_NAME'\n  KEY: 'YOUR_API_KEY'\n  SECRET: 'YOUR_API_SECRET'\n  URL: 'https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/'\n  TIMEOUT: 60\n  REQUEST_INTERVAL: 3\n  CHUNK_CHARS: 2000\n",
    docs: "configuration.md#textra",
  },
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

export function createBackendSetup(refresh, selectBackend) {
  const cards = new Map();
  let checking = false, busy = false;

  function snippet(label, value) {
    const group = node("div", "setup-snippet");
    const heading = node("div", "section-heading");
    heading.append(node("h4", "", label));
    const copy = node("button", "text-button", "コピー");
    copy.type = "button";
    copy.setAttribute("aria-label", `${label}をコピー`);
    const text = node("textarea", "setup-code");
    text.value = value;
    text.readOnly = true;
    text.rows = value.trimEnd().split("\n").length;
    text.setAttribute("aria-label", label);
    text.spellcheck = false;
    const status = node("p", "field-note");
    status.setAttribute("role", "status");
    copy.addEventListener("click", async () => {
      try {
        if (window.isSecureContext && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          status.textContent = "コピーしました。サーバー上で内容を確認して使ってください。";
        } else {
          text.focus(); text.select();
          status.textContent = document.execCommand("copy") ? "コピーしました。" : "選択しました。コピー操作で取得してください。";
        }
      } catch {
        text.focus(); text.select();
        status.textContent = "コピーできませんでした。選択した内容を手動でコピーしてください。";
      }
    });
    heading.append(copy);
    group.append(heading, text, status);
    return group;
  }

  for (const [id, guide] of Object.entries(GUIDES)) {
    const details = node("details", "backend-setup-card");
    details.dataset.backend = id;
    const summary = node("summary");
    summary.append(node("span", "", guide.label));
    const badge = node("span", "status-badge", "未確認");
    summary.append(badge);
    const reason = node("p", "setup-reason", "「設定を再確認」でサーバーの設定状況を確認できます。");
    const fields = node("p", "field-note");
    const steps = node("ol", "setup-steps");
    for (const text of guide.steps) steps.append(node("li", "", text));
    details.append(summary, reason, fields, node("p", "", guide.needs), steps);
    if (guide.command) details.append(snippet(`${guide.label}：モデル取得コマンド`, guide.command));
    details.append(snippet(`${guide.label}：NERの設定例`, guide.config));
    if (guide.docker) {
      const docker = node("details", "setup-docker");
      docker.append(node("summary", "", "Dockerで使う場合"), node("p", "", guide.docker));
      if (guide.dockerConfig) docker.append(snippet(`${guide.label}：Docker用の設定例`, guide.dockerConfig));
      const link = node("a", "text-button", "Dockerの詳細手順 ↗");
      link.href = DOCS + "docker.md";
      link.target = "_blank"; link.rel = "noopener noreferrer";
      docker.append(link);
      details.append(docker);
    }
    details.append(node("p", "field-note", "設定を保存したら、上の「翻訳方式の設定を再確認」を押してください。設定の確認後、小さなPDFで実際の翻訳を試せます。"));
    const actions = node("div", "setup-actions");
    const docs = node("a", "text-button", "詳細ドキュメント（GitHub）↗");
    docs.href = DOCS + guide.docs; docs.target = "_blank"; docs.rel = "noopener noreferrer";
    const use = node("button", "secondary-button", "この方式で翻訳する");
    use.type = "button";
    use.hidden = true;
    use.addEventListener("click", () => selectBackend(id));
    actions.append(docs, use);
    details.append(actions);
    $("backend-setup-list").append(details);
    cards.set(id, { details, summary, badge, reason, fields, use, configured: false });
  }
  $("setup-refresh").addEventListener("click", refresh);

  function controls() {
    $("setup-refresh").disabled = checking || busy;
    $("setup-refresh").textContent = checking ? "確認しています…" : "翻訳方式の設定を再確認";
    $("setup-refresh").setAttribute("aria-busy", String(checking));
    for (const card of cards.values()) card.use.disabled = checking || busy || !card.configured;
  }
  return {
    focus(id) {
      const card = cards.get(id);
      if (!card) return;
      card.details.open = true;
      card.summary.focus({ preventScroll: true });
      card.details.scrollIntoView({ block: "start" });
    },
    setBusy(value, loading) { busy = value; checking = loading; controls(); },
    status(text, error = false) {
      $("setup-status").textContent = text;
      $("setup-status").classList.toggle("is-error", error);
    },
    render(cap) {
      const links = [];
      for (const backend of cap.backends) {
        const card = cards.get(backend.id);
        if (!card) continue;
        card.configured = backend.configured;
        card.badge.textContent = backend.configured ? "設定済み・動作未確認" :
          ["MISSING_CONFIGURATION", "CONFIGURATION_UNREADABLE"].includes(backend.setup?.code) ? "未設定" : "要確認";
        card.badge.className = `status-badge ${backend.configured ? "succeeded" : "warning"}`;
        card.reason.textContent = backend.setup?.message || (backend.configured
          ? "設定済みです。実際の翻訳で動作を確認してください。" : GUIDES[backend.id].needs);
        card.fields.textContent = backend.setup?.fields?.length ? `確認する設定項目：${backend.setup.fields.join("、")}` : "";
        card.use.hidden = !backend.configured;
        if (!backend.configured) {
          const item = node("li");
          const link = node("a", "text-button", `${backend.label}の準備手順 →`);
          link.href = `#settings/${backend.id}`;
          item.append(link, node("span", "field-note", backend.setup?.message || GUIDES[backend.id].needs));
          links.push(item);
        }
      }
      $("backend-setup-links").replaceChildren(...links);
      $("backend-setup-links").hidden = !links.length;
      $("config-file-status").textContent = cap.configuration?.message || "";
      controls();
    },
    invalidate() {
      for (const card of cards.values()) {
        card.configured = false;
        card.badge.textContent = "未確認";
        card.badge.className = "status-badge";
        card.use.hidden = true;
        card.reason.textContent = "設定状況を確認できませんでした。準備手順を確認し、設定の再確認をお試しください。";
        card.fields.textContent = "";
      }
      $("config-file-status").textContent = "設定状況を確認できていません。";
      controls();
    },
  };
}
