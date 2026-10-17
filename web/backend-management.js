import { $, request, size } from "./api.js";

const SCHEMA = {
  INDEX: [["BASE_URL", "API接続先（/v1まで）", "url", true], ["MODEL", "モデル名", "text", true],
    ["API_KEY", "APIキー（Bearer認証・任意）", "password"], ["MAX_TOKENS", "最大出力トークン数", "number"],
    ["TIMEOUT", "タイムアウト（秒）", "number"], ["CHUNK_CHARS", "1チャンクの文字数", "number"]],
  GOOGLE: [["URL", "API接続先", "url", true], ["TIMEOUT", "タイムアウト（秒）", "number"],
    ["REQUEST_INTERVAL", "リクエスト間隔（秒）", "number"], ["CHUNK_CHARS", "1チャンクの文字数", "number"]],
  TEXTRA: [["URL", "英日翻訳APIの接続先", "url", true], ["NAME", "アカウント名", "text", true],
    ["KEY", "APIキー", "password", true], ["SECRET", "APIシークレット", "password", true],
    ["TIMEOUT", "タイムアウト（秒）", "number"], ["REQUEST_INTERVAL", "リクエスト間隔（秒）", "number"],
    ["CHUNK_CHARS", "1チャンクの文字数", "number"]],
  BERGAMOT: [["BINARY", "translator-cliの実行ファイル", "text"], ["WORKER_BINARY", "常駐ワーカー（指定時はこちらを優先）", "text"],
    ["MODEL_CONFIG", "英日モデルのconfig.yml", "text", true], ["NUM_WORKERS", "CPUスレッド数", "number"],
    ["TIMEOUT", "タイムアウト（秒）", "number"], ["CHUNK_CHARS", "1チャンクの文字数", "number"]],
};
const ACTIVE = new Set(["queued", "running", "cancelling"]);
const json = value => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

export function createBackendManagement(refresh) {
  const forms = new Map();
  let revision = null, loaded = false, loading = false, saving = false, dirty = false;
  let download = null, timer = null, starting = false, downloadVersion = 0;

  for (const [backend, fields] of Object.entries(SCHEMA)) {
    const form = node("form", "backend-config-form");
    form.dataset.backend = backend;
    form.append(node("h3", "", "接続・モデル設定"));
    const inputs = new Map();
    for (const [key, title, type, required] of fields) {
      const group = node("div", "backend-config-field");
      const input = node("input");
      input.type = type;
      input.id = `config-${backend}-${key}`;
      input.name = key;
      input.autocomplete = type === "password" ? "new-password" : "off";
      if (type === "number") { input.min = key === "REQUEST_INTERVAL" ? "0" : "1"; input.step = ["TIMEOUT", "REQUEST_INTERVAL"].includes(key) ? "any" : "1"; }
      if (type !== "number") input.maxLength = 4096;
      input.required = Boolean(required);
      const label = node("label", "field-label", title);
      label.htmlFor = input.id;
      group.append(label, input);
      const note = node("p", "field-note");
      if (type === "password") { group.append(note); input.placeholder = "未設定"; }
      inputs.set(key, { input, note, required, type });
      form.append(group);
    }
    let clearKey = null;
    if (backend === "INDEX") {
      const label = node("label", "setup-checkbox");
      clearKey = node("input"); clearKey.type = "checkbox";
      clearKey.addEventListener("change", () => { inputs.get("API_KEY").input.disabled = clearKey.checked; dirty = true; });
      label.append(clearKey, document.createTextNode("保存済みのAPIキーを削除する"));
      form.append(label);
    }
    const defaultLabel = node("label", "setup-checkbox");
    const makeDefault = node("input"); makeDefault.type = "checkbox";
    makeDefault.addEventListener("change", () => { makeDefault.dataset.dirty = "true"; });
    defaultLabel.append(makeDefault, document.createTextNode("この方式を既定にする"));
    form.append(defaultLabel);
    const save = node("button", "primary-button", "設定を保存"); save.type = "submit"; save.disabled = true;
    const message = node("p", "field-note"); message.setAttribute("role", "status");
    form.append(save, message);
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (saving || !loaded || !form.reportValidity()) return;
      void saveBackend(backend);
    });
    const details = document.querySelector(`.backend-setup-card[data-backend="${backend}"]`);
    details.insertBefore(form, details.querySelector('.setup-steps'));
    forms.set(backend, { form, inputs, save, message, makeDefault, clearKey });
  }

  const panel = node("section", "model-download-panel");
  panel.append(node("h3", "", "Indexモデルをダウンロード"),
    node("p", "field-note", "公式配布元からNERサーバーへ保存します。完了後に互換モデルサーバーを起動し、上のAPI接続先を保存してください。別ホストのモデルサーバーで使う場合は、取得したファイルの共有・転送が必要です。"));
  const select = node("select"); select.id = "index-download-format"; select.disabled = true;
  select.setAttribute("aria-label", "Indexモデルの形式");
  const actions = node("div", "setup-actions");
  const start = node("button", "secondary-button", "モデルを取得"); start.type = "button"; start.id = "index-download-start"; start.disabled = true;
  const cancel = node("button", "text-button", "取得を停止"); cancel.type = "button"; cancel.hidden = true; cancel.id = "index-download-cancel";
  const status = node("p", "field-note"); status.id = "index-download-status"; status.setAttribute("role", "status");
  const meter = node("progress", "download-progress"); meter.id = "index-download-progress"; meter.max = 100; meter.hidden = true;
  const result = node("pre", "review-text"); result.id = "index-download-result"; result.hidden = true;
  actions.append(start, cancel);
  panel.append(select, actions, status, meter, result);
  forms.get("INDEX").form.after(panel);

  function controls() {
    for (const item of forms.values()) {
      for (const { input } of item.inputs.values()) input.disabled = saving || loading;
      item.save.disabled = saving || loading || !loaded;
      item.makeDefault.disabled = saving || loading;
      if (item.clearKey) {
        item.clearKey.disabled = saving || loading;
        item.inputs.get("API_KEY").input.disabled ||= item.clearKey.checked;
      }
    }
  }
  function apply(data, backend = null) {
    revision = data.revision;
    for (const [id, item] of forms) {
      if (backend && backend !== id) {
        if (item.makeDefault.dataset.dirty !== "true") item.makeDefault.checked = data.default_backend === id;
        continue;
      }
      const current = data.backends[id];
      item.makeDefault.checked = data.default_backend === id;
      item.makeDefault.dataset.dirty = "false";
      if (item.clearKey) item.clearKey.checked = false;
      for (const [key, field] of item.inputs) {
        if (field.type === "password") {
          field.input.value = "";
          field.input.required = Boolean(field.required && !current.secrets_set[key]);
          field.input.placeholder = current.secrets_set[key] ? "登録済み（変更する場合のみ入力）" : "未設定";
          field.note.textContent = current.secrets_set[key] ? "保存済みです。空欄なら現在の値を維持します。" : "";
        } else field.input.value = current.values[key] ?? "";
      }
    }
    loaded = true;
  }
  async function saveBackend(backend) {
    const item = forms.get(backend);
    const values = {};
    for (const [key, field] of item.inputs) {
      if (field.type === "password") { if (field.input.value) values[key] = field.input.value; }
      else values[key] = field.type === "number" ? Number(field.input.value) : field.input.value.trim();
    }
    if (item.clearKey?.checked) values.API_KEY = "";
    saving = true; controls();
    item.message.textContent = "保存しています…";
    item.message.classList.remove("is-error");
    try {
      const data = await request(`/api/backend-settings/${backend}`, { method: "PUT", ...json({ revision, values, make_default: item.makeDefault.checked }) });
      apply(data, backend);
      dirty = [...forms.values()].some(other => other !== item && other.form.dataset.dirty === "true");
      item.form.dataset.dirty = "false";
      item.message.textContent = "保存しました。次に開始する翻訳から適用します。";
      await refresh();
    } catch (error) {
      item.message.textContent = error.message;
      item.message.classList.add("is-error");
    } finally { saving = false; controls(); }
  }
  for (const item of forms.values()) item.form.addEventListener("input", () => { item.form.dataset.dirty = "true"; });

  function renderDownload(job) {
    const current = ++downloadVersion;
    clearTimeout(timer);
    download = job;
    const active = job && ACTIVE.has(job.status);
    select.disabled = starting || active;
    start.disabled = starting || active || !select.options.length;
    cancel.hidden = !active;
    cancel.disabled = job?.status === "cancelling";
    meter.hidden = !job;
    result.hidden = job?.status !== "succeeded";
    if (!job) { status.textContent = "形式を選んで取得を開始できます。"; return; }
    if (job.total_bytes) meter.value = Math.min(100, job.downloaded_bytes * 100 / job.total_bytes);
    else meter.removeAttribute("value");
    const stages = { queued: "取得を待っています", metadata: "配布情報を確認しています", downloading: "ダウンロード中", verifying: "ファイルを検証しています", done: "取得完了" };
    status.textContent = job.status === "failed" ? job.error?.message || "取得に失敗しました。" :
      job.status === "cancelled" ? "停止しました。再度取得すると途中のファイルを再利用します。" :
        job.status === "cancelling" ? "停止しています…" : job.status === "succeeded" ? "取得完了。サイズ・チェックサムを確認しました。" :
          `${stages[job.stage] || "取得中"}${job.total_bytes ? ` · ${size(job.downloaded_bytes)} / ${size(job.total_bytes)}` : ""}${job.file ? ` · ${job.file}` : ""}`;
    status.classList.toggle("is-error", job.status === "failed");
    if (job.status === "succeeded") {
      const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
      const path = quote(job.result.model_path);
      const command = job.variant === "hf" ? `vllm serve ${path} --served-model-name IndexTeam/Index-Translate-2B --host <待受IP> --port 8001` :
        `llama-server -m ${path} --alias IndexTeam/Index-Translate-2B --host <待受IP> --port 8001 -ngl 0 -c 8192 --jinja --chat-template-kwargs '{"enable_thinking":false}'`;
      result.textContent = `保存先：${job.result.directory}\n\nモデルサーバーの起動例（実行環境の準備後、<待受IP>を配置に合わせて変更）：\n${command}`;
    }
    if (active) timer = setTimeout(() => void poll(job.id, current), 1000);
  }
  async function poll(id, current) {
    try {
      const job = await request(`/api/model-downloads/${id}`);
      if (current === downloadVersion) renderDownload(job);
    }
    catch (error) {
      if (current !== downloadVersion) return;
      status.textContent = `取得状況を確認できません。${error.message}自動で再確認します。`;
      timer = setTimeout(() => void poll(id, current), 3000);
    }
  }
  start.addEventListener("click", async () => {
    if (starting || ACTIVE.has(download?.status)) return;
    starting = true; start.disabled = select.disabled = true;
    status.textContent = "取得を受け付けています…";
    try { renderDownload(await request('/api/model-downloads', { method: "POST", ...json({ variant: select.value }) })); }
    catch (error) { status.textContent = error.message; status.classList.add("is-error"); }
    finally { starting = false; start.disabled = ACTIVE.has(download?.status); select.disabled = start.disabled; }
  });
  cancel.addEventListener("click", async () => {
    downloadVersion++;
    clearTimeout(timer);
    cancel.disabled = true;
    try { renderDownload(await request(`/api/model-downloads/${download.id}/cancel`, { method: "POST" })); }
    catch (error) { status.textContent = error.message; cancel.disabled = false; void poll(download.id, downloadVersion); }
  });

  async function load(force = false) {
    if (loading || saving || loaded && !force) return;
    if (force && dirty && !window.confirm("未保存の入力を破棄し、保存済み設定を読み直しますか？")) return;
    loading = true; controls();
    $("backend-config-message").textContent = "保存済み設定を読み込んでいます…";
    try {
      apply(await request('/api/backend-settings'));
      dirty = false;
      for (const item of forms.values()) item.form.dataset.dirty = "false";
      $("backend-config-message").textContent = "設定はこの画面から保存できます。APIキーなどの秘密情報は再表示しません。";
    } catch (error) { $("backend-config-message").textContent = error.message; }
    finally { loading = false; controls(); }
    try {
      const data = await request('/api/model-downloads');
      const previous = select.value;
      select.replaceChildren(...data.catalog.map(item => {
        const option = node("option", "", `${item.label} · モデル本体 約${size(item.size_bytes)}`);
        option.value = item.variant; return option;
      }));
      if ([...select.options].some(option => option.value === previous)) select.value = previous;
      renderDownload(data.items.find(item => ACTIVE.has(item.status)) || data.items[0] || null);
    } catch (error) { status.textContent = error.message; start.disabled = true; }
  }
  $("backend-config-reload").addEventListener("click", () => void load(true));
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  return { load };
}
