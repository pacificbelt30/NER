import { $, ACTIVE, size, wait, request, createSubmissionKey } from "./api.js";
import { progress, renderJob as renderJobView } from "./job-view.js";
import { createPreview } from "./preview.js";
import { createHistory } from "./history.js";
import { createSettings } from "./settings.js";
import { initializeAuth, loginRequired } from "./auth.js";
const STORAGE = "ner:last-job:v1";
const state = {
  file: null,
  blob: null,
  job: null,
  key: null,
  xhr: null,
  capabilities: null,
  busy: false,
  version: 0,
};
const preview = createPreview(() => state.job, renderJob, expiredJob);
const history = createHistory(async id => {
  if (state.xhr) throw new Error("アップロードが完了してから履歴を開いてください。");
  const job = await request(`/api/jobs/${id}`);
  if (state.xhr) throw new Error("アップロードが完了してから履歴を開いてください。");
  newFile();
  selectJobBackend(job);
  track(job);
  location.hash = "translate";
  showView();
  $("status-card").scrollIntoView({ behavior: "smooth", block: "start" });
});
const settings = createSettings();

function showView() {
  const view = ["history", "settings"].includes(location.hash.slice(1))
    ? location.hash.slice(1) : "translate";
  for (const name of ["translate", "history", "settings"]) {
    $(`${name}-view`).hidden = name !== view;
    if (name === view) $(`nav-${name}`).setAttribute("aria-current", "page");
    else $(`nav-${name}`).removeAttribute("aria-current");
  }
  document.title = `${view === "history" ? "翻訳履歴" : view === "settings" ? "設定" : "PDF翻訳"} — NER`;
  if (view === "history") void history.load();
  if (view === "settings") void settings.load();
}
window.addEventListener("hashchange", showView);

function renderJob(job) {
  const previousStatus = state.job?.status;
  renderJobView(state, job, { controls, remember, showResults: preview.render });
  $("upload-panel").hidden = job.status === "succeeded" || ACTIVE.has(job.status);
  if (previousStatus !== job.status && location.hash === "#history") void history.load();
}

function remember(value) {
  try {
    value
      ? localStorage.setItem(STORAGE, JSON.stringify(value))
      : localStorage.removeItem(STORAGE);
  } catch {}
}
function recalled() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE) || "null");
  } catch {
    return null;
  }
}
function setError(message) {
  $("form-error").textContent = message || "";
  $("form-error").hidden = !message;
}
function connection(message) {
  if ($("connection-note").textContent !== (message || "")) $("connection-note").textContent = message || "";
  $("connection-note").hidden = !message;
}
function controls(busy) {
  state.busy = busy;
  $("pdf-file").disabled = busy;
  $("dropzone").classList.toggle("locked", busy);
  $("backend").disabled =
    busy || !state.capabilities?.backends.some((b) => b.configured);
  $("start-button").disabled = busy || !state.file || $("backend").disabled;
  $("remove-file").disabled = busy;
  $("new-file-button").disabled = busy;
  $("refresh-settings").disabled = busy;
}

function backendNote() {
  const backend = state.capabilities?.backends.find(
    (b) => b.id === $("backend").value,
  );
  $("backend-note").textContent = backend?.local
    ? "本文を外部の翻訳サービスへ送らず、サーバー上で翻訳します。"
    : "本文を外部の翻訳サービスへ送信して翻訳します。";
}

function selectJobBackend(job) {
  if (state.capabilities?.backends.find(b => b.id === job.backend)?.configured)
    $("backend").value = job.backend;
  backendNote();
}

async function loadCapabilities() {
  try {
    const cap = await request("/api/capabilities");
    const previous = $("backend").value;
    state.capabilities = cap;
    $("backend").replaceChildren(
      ...cap.backends.map((backend) => {
        const option = document.createElement("option");
        option.value = backend.id;
        option.disabled = !backend.configured;
        option.textContent = `${backend.label} · ${backend.local ? "ローカル" : "オンライン"}${backend.configured ? "" : "（準備が必要）"}`;
        return option;
      }),
    );
    const available = cap.backends.filter((b) => b.configured).map((b) => b.id);
    $("backend").value =
      [previous, state.job?.backend, cap.default_backend, ...available].find(
        (id) => available.includes(id),
      ) || "";
    $("setup-message").hidden = available.length > 0;
    $("setup-message").textContent =
      "設定済みの翻訳方式がありません。サーバーの翻訳設定を確認してから「設定を再確認」を押してください。";
    $("file-rule").textContent = `PDF · 最大${size(cap.max_upload_bytes)}`;
    backendNote();
    controls(state.busy);
  } catch (error) {
    $("setup-message").textContent = error.message;
    $("setup-message").hidden = false;
    $("start-button").disabled = true;
  }
}

function chooseFile(file) {
  if (state.busy) return;
  const limit = state.capabilities?.max_upload_bytes || 50 * 1024 * 1024;
  if (!file || !/\.pdf$/i.test(file.name)) {
    setError("PDFファイルを選んでください。");
    return;
  }
  if (!file.size) {
    setError("PDFが空です。別のファイルを選んでください。");
    return;
  }
  if (file.size > limit) {
    setError(`PDFは${size(limit)}以内にしてください。`);
    return;
  }
  resetJob();
  if (state.blob) URL.revokeObjectURL(state.blob);
  state.file = file;
  state.blob = URL.createObjectURL(file);
  state.key = null;
  $("file-name").textContent = file.name;
  $("file-description").textContent =
    `${size(file.size)} · 翻訳する準備ができました`;
  $("file-actions").hidden = false;
  $("local-preview").hidden = true;
  setError("");
  controls(false);
}

function resetJob() {
  state.version++;
  state.job = null;
  state.key = null;
  preview.clear();
  remember(null);
  $("status-card").hidden = true;
  $("results").hidden = true;
  $("upload-panel").hidden = false;
  $("original-frame").removeAttribute("src");
  $("translated-frame").removeAttribute("src");
  connection("");
}

function newFile() {
  if (state.xhr) return;
  resetJob();
  if (state.blob) URL.revokeObjectURL(state.blob);
  state.file = null;
  state.blob = null;
  $("pdf-file").value = "";
  $("file-name").textContent = "PDFをここにドロップ";
  $("file-description").textContent = "またはクリックしてファイルを選択";
  $("file-actions").hidden = true;
  $("local-preview").hidden = true;
  $("local-frame").removeAttribute("src");
  setError("");
  controls(false);
  $("pdf-file").focus();
}

function expiredJob(error) {
  if (![404, 410].includes(error.status)) {
    connection(error.message);
    return;
  }
  state.version++;
  controls(false);
  remember(null);
  $("status-card").hidden = false;
  $("status-title").textContent = "結果の保存期限が過ぎたか、削除されています";
  $("progress-detail").textContent =
    "PDFをもう一度アップロードして翻訳してください。";
  $("cancel-button").hidden = true;
  $("retry-button").hidden = true;
  $("new-file-button").hidden = false;
  $("results").hidden = true;
  progress(0);
}

async function poll(version) {
  let failures = 0;
  while (
    version === state.version &&
    state.job &&
    ACTIVE.has(state.job.status)
  ) {
    await wait(failures ? Math.min(16000, 1000 * 2 ** failures) : 1000);
    if (version !== state.version) return;
    try {
      const job = await request(`/api/jobs/${state.job.id}`);
      if (version !== state.version) return;
      failures = 0;
      connection("");
      renderJob(job);
    } catch (error) {
      if (version !== state.version) return;
      if ([404, 410].includes(error.status)) {
        expiredJob(error);
        return;
      }
      failures++;
      connection(
        "接続を確認しています。受付済みの翻訳は継続します。自動で再接続します。",
      );
    }
  }
}

function track(job) {
  renderJob(job);
  connection("");
  const version = ++state.version;
  if (ACTIVE.has(job.status)) void poll(version);
}

function upload() {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open("POST", "/api/jobs");
    xhr.timeout = 300000;
    xhr.setRequestHeader("Idempotency-Key", state.key);
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        progress((event.loaded / event.total) * 100);
        $("progress-detail").textContent =
          `${size(event.loaded)} / ${size(event.total)} をアップロード中`;
        if (event.loaded === event.total)
          $("status-title").textContent = "PDFを受け付けています";
      }
    });
    xhr.onload = () => {
      let body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("受付の応答を確認できませんでした。"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else {
        if (xhr.status === 401) loginRequired();
        const error = new Error(
          body.error?.message || "PDFを受け付けられませんでした。",
        );
        error.status = xhr.status;
        reject(error);
      }
    };
    xhr.onerror = xhr.ontimeout = () => {
      const error = new Error("アップロードの通信を確認できませんでした。");
      error.network = true;
      reject(error);
    };
    xhr.onabort = () => {
      const error = new Error("アップロードを停止しました。");
      error.aborted = true;
      reject(error);
    };
    const form = new FormData();
    form.append("file", state.file);
    form.append("backend", $("backend").value);
    form.append("output", "comparison");
    xhr.send(form);
  });
}

async function start() {
  if (!state.file || state.busy || !$("backend").value) return;
  if (state.job && !ACTIVE.has(state.job.status)) resetJob();
  setError("");
  try {
    state.key ||= createSubmissionKey();
    remember({ key: state.key });
    connection("");
    controls(true);
    $("status-card").hidden = false;
    $("status-card").className = "status-card";
    $("status-title").textContent = "PDFをアップロードしています";
    $("status-label").textContent = "UPLOADING";
    $("status-filename").textContent = state.file.name;
    $("status-hint").textContent =
      "アップロードが終わるまで、このページを開いたままにしてください。";
    $("cancel-button").hidden = false;
    $("cancel-button").disabled = false;
    $("cancel-button").textContent = "アップロードを停止";
    $("retry-button").hidden = true;
    $("new-file-button").hidden = true;
    progress(0);
    track(await upload());
  } catch (error) {
    if (state.key && (error.network || error.aborted || !error.status)) {
      try {
        const job = await request(`/api/jobs/by-key/${state.key}`);
        if (error.aborted && ACTIVE.has(job.status))
          track(
            await request(`/api/jobs/${job.id}/cancel`, { method: "POST" }),
          );
        else track(job);
        return;
      } catch {
        /* Keep the same submission key if receipt could not be confirmed. */
      }
    }
    controls(false);
    setError(error.message);
    $("status-card").hidden = false;
    $("status-title").textContent = error.aborted
      ? "アップロードを停止しました"
      : "受付を完了できませんでした";
    $("progress-detail").textContent = error.aborted
      ? "PDFを選び直すか、もう一度送信できます。"
      : "同じPDFを再送しても、受付済みの翻訳は重複して作成されません。";
    $("cancel-button").hidden = true;
    $("new-file-button").hidden = false;
    $("retry-button").hidden = !!error.aborted;
    $("retry-button").textContent = "受付を確認して再送";
    progress(0);
  } finally {
    state.xhr = null;
  }
}

$("translate-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void start();
});
$("pdf-file").addEventListener("change", (event) => {
  if (event.target.files[0]) chooseFile(event.target.files[0]);
});
for (const name of ["dragenter", "dragover"])
  $("dropzone").addEventListener(name, (event) => {
    event.preventDefault();
    if (!state.busy) $("dropzone").classList.add("dragging");
  });
for (const name of ["dragleave", "drop"])
  $("dropzone").addEventListener(name, (event) => {
    event.preventDefault();
    $("dropzone").classList.remove("dragging");
    if (name === "drop" && !state.busy) {
      if (event.dataTransfer.files.length !== 1)
        setError("PDFを1ファイルだけ選んでください。");
      else chooseFile(event.dataTransfer.files[0]);
    }
  });
$("backend").addEventListener("change", backendNote);
$("refresh-settings").addEventListener("click", () => void loadCapabilities());
$("remove-file").addEventListener("click", newFile);
$("new-file-button").addEventListener("click", newFile);
document.querySelectorAll('a[href="#translate"]').forEach(link => {
  link.addEventListener("click", newFile);
});
$("preview-original").addEventListener("click", () => {
  if (state.blob) {
    $("local-frame").src = state.blob;
    $("open-local-original").href = state.blob;
    $("local-preview").hidden = false;
    $("local-preview").scrollIntoView({ behavior: "smooth" });
  }
});
$("close-preview").addEventListener("click", () => {
  $("local-preview").hidden = true;
});
$("cancel-button").addEventListener("click", async () => {
  if (state.xhr) {
    state.xhr.abort();
    return;
  }
  if (!state.job) return;
  $("cancel-button").disabled = true;
  try {
    track(
      await request(`/api/jobs/${state.job.id}/cancel`, { method: "POST" }),
    );
  } catch (error) {
    connection(error.message);
    $("cancel-button").disabled = false;
  }
});
$("retry-button").addEventListener("click", async () => {
  if (!state.job) {
    await start();
    return;
  }
  $("retry-button").disabled = true;
  setError("");
  controls(true);
  const form = new FormData();
  if ($("backend").value) form.append("backend", $("backend").value);
  form.append("output", "comparison");
  try {
    track(
      await request(`/api/jobs/${state.job.id}/retry`, {
        method: "POST",
        body: form,
      }),
    );
  } catch (error) {
    setError(error.message);
    connection(error.message);
    controls(false);
    $("retry-button").disabled = false;
  }
});

async function restore(saved) {
  state.key = saved.key || null;
  const version = ++state.version;
  let failures = 0;
  while (version === state.version) {
    try {
      const job = await request(
        saved.id ? `/api/jobs/${saved.id}` : `/api/jobs/by-key/${saved.key}`,
      );
      if (version !== state.version) return;
      selectJobBackend(job);
      track(job);
      return;
    } catch (error) {
      if (version !== state.version) return;
      if ([404, 410].includes(error.status)) {
        remember(null);
        if (saved.id) expiredJob(error);
        return;
      }
      failures++;
      connection("前回の翻訳への接続を確認しています。自動で再接続します。");
      $("status-card").hidden = false;
      $("status-title").textContent = "前回の翻訳を確認しています";
      $("cancel-button").hidden = true;
      $("new-file-button").hidden = false;
      progress(null);
      await wait(Math.min(16000, 1000 * 2 ** failures));
    }
  }
}

(async function initialize() {
  try {
    if (!await initializeAuth()) return;
  } catch (error) {
    $("account-message").textContent = error.message;
    $("account-message").hidden = false;
    return;
  }
  showView();
  if (location.hash !== "#settings") void settings.load();
  await loadCapabilities();
  const saved = recalled();
  if (saved?.id || saved?.key) await restore(saved);
})();
