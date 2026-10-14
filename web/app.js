"use strict";

const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["queued", "running", "cancelling"]);
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
  expiryTimer: null,
  previewPage: 1,
  previewVersion: 0,
};

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
function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  const value = bytes / (1024 * 1024);
  return `${Number.isInteger(value) ? value : value.toFixed(1)} MiB`;
}
function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
function selectedOutput() {
  return document.querySelector('input[name="output"]:checked').value;
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(12000),
      ...options,
    });
  } catch {
    const error = new Error(
      "サーバーとの通信を確認できません。接続を確認してください。",
    );
    error.network = true;
    throw error;
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "サーバーの応答を読み取れませんでした。もう一度お試しください。",
    );
  }
  if (!response.ok) {
    const error = new Error(
      body.error?.message || body.detail || "処理に失敗しました。",
    );
    error.code = body.error?.code;
    error.status = response.status;
    throw error;
  }
  return body;
}

function controls(busy) {
  state.busy = busy;
  $("pdf-file").disabled = busy;
  $("dropzone").classList.toggle("locked", busy);
  $("backend").disabled =
    busy || !state.capabilities?.backends.some((b) => b.configured);
  document.querySelectorAll('input[name="output"]').forEach((input) => {
    input.disabled = busy;
  });
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
  state.previewPage = 1;
  state.previewVersion++;
  clearTimeout(state.expiryTimer);
  remember(null);
  $("status-card").hidden = true;
  $("results").hidden = true;
  $("original-frame").removeAttribute("src");
  $("translated-frame").removeAttribute("src");
  connection("");
}

function newFile() {
  if (state.busy) return;
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

function progress(percent) {
  const indeterminate = percent == null;
  $("progress-track").classList.toggle("indeterminate", indeterminate);
  if (indeterminate) {
    $("progress-fill").style.width = "";
    $("progress-track").removeAttribute("aria-valuenow");
    $("progress-percent").textContent = "";
  } else {
    const bounded = Math.max(0, Math.min(100, percent));
    $("progress-track").setAttribute("aria-valuenow", String(bounded));
    $("progress-fill").style.width = `${bounded}%`;
    $("progress-percent").textContent = `${Math.floor(bounded)}%`;
  }
}

function renderJob(job) {
  state.job = job;
  remember({ id: job.id, key: state.key });
  $("status-card").hidden = false;
  $("status-card").className = `status-card ${job.status}`;
  $("status-filename").textContent = job.filename;
  const active = ACTIVE.has(job.status);
  controls(active);
  $("cancel-button").hidden = !active;
  $("cancel-button").disabled = job.status === "cancelling";
  $("cancel-button").textContent =
    job.status === "cancelling" ? "停止しています…" : "翻訳を停止";
  $("retry-button").hidden = !(
    job.status === "cancelled" ||
    (job.status === "failed" && job.error?.retryable)
  );
  $("retry-button").textContent = "同じPDFで再試行";
  $("retry-button").disabled = false;
  $("new-file-button").hidden = active;
  const {
    stage,
    completed_blocks: done,
    total_blocks: total,
    page,
    pages,
    completed_chunks: chunksDone = 0,
    total_chunks: chunksTotal = 0,
  } = job.progress;
  const warningCount = job.result_summary?.warning_count || job.warnings?.length || 0;
  const withWarnings = job.status === "succeeded" && warningCount > 0;
  const hasReport = (job.result_summary?.report_pages || 0) > 0;
  const warningDetail = hasReport
    ? "原文を保持した箇所とエラー内容はPDFの注釈・末尾で確認できます。"
    : (job.warnings || []).map(warning => warning.message).join(" ");
  const titles = {
    queued: "順番を待っています",
    running: "日本語に翻訳しています",
    cancelling: "翻訳を停止しています",
    succeeded: withWarnings ? "注意付きPDFを作成しました" : "翻訳が完了しました",
    failed: "翻訳を完了できませんでした",
    cancelled: "翻訳を停止しました",
  };
  if ($("status-title").textContent !== titles[job.status]) $("status-title").textContent = titles[job.status];
  const statusLabel =
    {
      succeeded: withWarnings ? "READY WITH NOTES" : "COMPLETED",
      failed: "PLEASE TRY AGAIN",
      cancelled: "STOPPED",
    }[job.status] || "TRANSLATING";
  if ($("status-label").textContent !== statusLabel) $("status-label").textContent = statusLabel;
  $("progress-detail").textContent =
    job.status === "queued"
      ? `待ち順：${job.queue_position || 1}番目`
      : job.status === "cancelling"
        ? "現在処理中の翻訳が終わると停止します。"
        : job.status === "failed"
          ? job.error?.message || "時間を置いてもう一度お試しください。"
          : job.status === "cancelled"
            ? "同じPDFで再試行するか、別のPDFを選べます。"
            : stage === "extracting"
              ? "PDFの本文を確認しています。"
              : stage === "saving"
                ? "訳文をPDFに配置しています。"
                : stage === "combining"
                  ? "原文と訳文の比較用PDFを作成しています。"
                  : stage === "previewing"
                    ? "読み比べるためのプレビューを準備しています。"
                    : job.status === "succeeded"
                      ? withWarnings
                        ? `${job.result_summary?.translated_blocks || 0} / ${job.result_summary?.total_blocks || total} ブロックを翻訳しました。${warningCount}件の注意点があります。${warningDetail}`
                        : `${pages}ページの翻訳が完了しました。`
                      : total
                        ? `${done} / ${total} ブロック · ${page || 1} / ${pages} ページ${chunksTotal > 1 ? ` · 分割 ${chunksDone} / ${chunksTotal}` : ""}`
                        : "翻訳を準備しています。";
  const completed = done + (stage === "translating" && chunksTotal ? Math.min(1, chunksDone / chunksTotal) : 0);
  const percent =
    job.status === "succeeded" ? 100 : total ? (Math.min(total, completed) / total) * 90 : null;
  progress(percent);
  $("status-hint").textContent = active
    ? "このページを開き直しても、処理中の翻訳を確認できます。"
    : job.status === "succeeded"
      ? withWarnings
        ? hasReport
          ? "PDFは保存できます。エラー一覧と配置できなかった訳文は、ダウンロードするPDFの末尾に掲載しています。"
          : "翻訳PDFは保存できます。上記の注意点を確認してください。"
        : "結果は完了後1時間保存されます。必要なPDFを保存してください。"
      : job.status === "failed" && !job.error?.retryable
        ? "別のPDFを選んでお試しください。"
        : "翻訳方式を変更して、同じPDFで再試行することもできます。";
  if (job.status === "succeeded") showResults(job);
}

function showResults(job) {
  $("results").hidden = false;
  const untranslated = job.result_summary?.outcome === "untranslated";
  $("results-title").textContent = untranslated ? "原文とエラーレポートを読む" : "翻訳したPDFを読む";
  $("download-translated").textContent = untranslated ? "原文・エラーPDFを保存 ↓" : "訳文PDFを保存 ↓";
  $("download-translated").href = job.downloads.translated;
  $("download-comparison").hidden = !job.downloads.comparison;
  if (job.downloads.comparison)
    $("download-comparison").href = job.downloads.comparison;
  for (const kind of ["original", "translated"]) {
    const url = `${job.downloads[kind]}?inline=true`;
    $(`open-${kind}`).href = url;
  }
  if ($("results").dataset.job !== job.id) {
    $("results").dataset.job = job.id;
    state.previewPage = 1;
    void showPage(1);
  }
  $("expiry-note").textContent =
    `保存期限：${new Date(job.expires_at).toLocaleString("ja-JP")}（このページを閉じても再取得できます）`;
  clearTimeout(state.expiryTimer);
  const remaining = Date.parse(job.expires_at) - Date.now();
  state.expiryTimer = setTimeout(
    () => {
      if (state.job?.id === job.id)
        request(`/api/jobs/${job.id}`).catch(expiredJob);
    },
    Math.max(0, remaining) + 100,
  );
}

async function showPage(page) {
  if (!state.job || state.job.status !== "succeeded") return;
  const pages = Math.max(1, state.job.progress.pages);
  state.previewPage = Math.max(1, Math.min(pages, page));
  const version = ++state.previewVersion;
  $("page-position").textContent = `${state.previewPage} / ${pages} ページ`;
  $("previous-page").disabled = true;
  $("next-page").disabled = true;
  $("preview-status").textContent = "ページを読み込んでいます…";
  const jobId = state.job.id;
  try {
    const images = await Promise.all(
      ["original", "translated"].map(
        (kind) =>
          new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve({ kind, image });
            image.onerror = () =>
              reject(
                new Error(
                  "ページを表示できませんでした。PDFを別タブで開くか、ダウンロードしてください。",
                ),
              );
            image.src = `/api/jobs/${jobId}/preview/${kind}?page=${state.previewPage}`;
          }),
      ),
    );
    if (version !== state.previewVersion) return;
    for (const { kind, image } of images) {
      $(`${kind}-frame`).src = image.src;
      $(`${kind}-frame`).alt =
        `${kind === "original" ? "原文" : "日本語訳"} ${state.previewPage}ページ`;
    }
    document.querySelectorAll(".page-scroll").forEach((pane) => {
      pane.scrollTop = 0;
      pane.scrollLeft = 0;
    });
    $("preview-status").textContent =
      "原文と日本語訳の同じページを表示しています。";
  } catch (error) {
    if (version === state.previewVersion)
      $("preview-status").textContent = error.message;
  } finally {
    if (version === state.previewVersion) {
      $("previous-page").disabled = state.previewPage === 1;
      $("next-page").disabled = state.previewPage === pages;
    }
  }
}

$("previous-page").addEventListener(
  "click",
  () => void showPage(state.previewPage - 1),
);
$("next-page").addEventListener(
  "click",
  () => void showPage(state.previewPage + 1),
);
$("preview-zoom").addEventListener("change", () => {
  document.querySelectorAll(".page-image").forEach((image) => {
    image.style.width = `${$("preview-zoom").value}%`;
  });
});

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
    form.append("output", selectedOutput());
    xhr.send(form);
  });
}

async function start() {
  if (!state.file || state.busy || !$("backend").value) return;
  if (state.job && !ACTIVE.has(state.job.status)) resetJob();
  state.key ||= crypto.randomUUID();
  remember({ key: state.key });
  setError("");
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
  try {
    track(await upload());
  } catch (error) {
    if (error.network || error.aborted || !error.status) {
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
  form.append("output", selectedOutput());
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
      if (
        state.capabilities?.backends.find((b) => b.id === job.backend)
          ?.configured
      )
        $("backend").value = job.backend;
      document.querySelector(
        `input[name="output"][value="${job.output}"]`,
      ).checked = true;
      backendNote();
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
  await loadCapabilities();
  const saved = recalled();
  if (saved?.id || saved?.key) await restore(saved);
})();
