import { $, ACTIVE } from "./api.js";

export function progress(percent) {
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

export function renderJob(state, job, { controls, remember, showResults }) {
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
        : job.expires_at
          ? "保存期間内は、翻訳履歴から結果を開いてダウンロードできます。"
          : "翻訳履歴から、いつでも結果を開いてダウンロードできます。"
      : job.status === "failed" && !job.error?.retryable
        ? "別のPDFを選んでお試しください。"
        : "翻訳方式を変更して、同じPDFで再試行することもできます。";
  if (job.status === "succeeded") showResults(job);
}

