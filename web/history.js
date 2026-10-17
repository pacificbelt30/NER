import { $, ACTIVE, request } from "./api.js";

const LABELS = {
  queued: "順番待ち", running: "翻訳中", cancelling: "停止中",
  succeeded: "完了", failed: "失敗", cancelled: "停止済み",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function createHistory(openJob) {
  let offset = 0;
  let version = 0;
  let debounce;
  let refreshTimer;
  const limit = 20;

  function row(job) {
    const article = element("article", "history-row");
    article.dataset.job = job.id;
    const icon = element("span", "file-icon", "PDF");
    icon.setAttribute("aria-hidden", "true");
    const content = element("div", "history-content");
    content.append(element("h3", "history-filename", job.filename));
    const date = new Date(job.created_at).toLocaleString("ja-JP", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
    content.append(element("p", "muted", `${date} · ${job.backend}`));
    if (job.kind === "review") content.append(element("p", "history-detail", "人が確認・修正したPDF"));
    const warningCount = job.result_summary?.warning_count || 0;
    const withWarnings = job.status === "succeeded" && warningCount > 0;
    const badge = element("span", `status-badge ${withWarnings ? "warning" : job.status}`,
      withWarnings ? `注意付き完了 · ${warningCount}件` : LABELS[job.status]);
    if (job.status === "failed") content.append(element("p", "history-detail", job.error?.message));
    if (job.result_summary?.outcome === "untranslated")
      content.append(element("p", "history-detail", "原文とエラーレポートを保存しています。"));
    const actions = element("div", "history-actions");
    const open = element("button", "secondary-button", ACTIVE.has(job.status) ? "進捗を確認" : "開く");
    open.type = "button";
    open.setAttribute("aria-label", `${job.filename}を開く`);
    open.addEventListener("click", async () => {
      open.disabled = true;
      try {
        await openJob(job.id);
      } catch (error) {
        $("history-message").textContent = error.message;
        if ([404, 410].includes(error.status)) void load();
      } finally {
        open.disabled = false;
      }
    });
    actions.append(open);
    for (const [kind, label] of [["translated", "訳文PDF"], ["original", "原文"], ["comparison", "比較PDF"]]) {
      if (!job.downloads?.[kind]) continue;
      const link = element("a", "text-button", `${label} ↓`);
      link.href = job.downloads[kind];
      link.download = "";
      link.setAttribute("aria-label", `${job.filename}の${label}を保存`);
      actions.append(link);
    }
    article.append(icon, content, badge, actions);
    return article;
  }

  async function load({ reset = false } = {}) {
    if (reset) offset = 0;
    const current = ++version;
    clearTimeout(refreshTimer);
    $("history-list").setAttribute("aria-busy", "true");
    $("history-refresh").disabled = true;
    $("history-message").textContent = "履歴を読み込んでいます…";
    const query = new URLSearchParams({ q: $("history-search").value,
      limit, offset });
    if ($("history-status").value) query.set("status", $("history-status").value);
    try {
      const result = await request(`/api/jobs?${query}`);
      if (current !== version) return;
      if (offset && !result.items.length) { offset = 0; void load(); return; }
      $("history-list").replaceChildren(...result.items.map(row));
      $("history-empty").hidden = result.total > 0;
      $("history-empty-title").textContent = $("history-search").value || $("history-status").value
        ? "条件に合う履歴がありません" : "翻訳履歴はまだありません";
      $("history-count").textContent = `${result.total}件`;
      $("history-message").textContent = "";
      $("history-pagination").hidden = result.total <= limit;
      $("history-position").textContent = `${offset + 1}–${offset + result.items.length} / ${result.total}件`;
      $("history-previous").disabled = offset === 0;
      $("history-next").disabled = offset + limit >= result.total;
      if (result.items.some(job => ACTIVE.has(job.status)))
        refreshTimer = setTimeout(() => { if (location.hash === "#history") void load(); }, 5000);
    } catch (error) {
      if (current === version) $("history-message").textContent = error.message;
    } finally {
      if (current === version) {
        $("history-list").setAttribute("aria-busy", "false");
        $("history-refresh").disabled = false;
      }
    }
  }

  $("history-search").addEventListener("input", () => {
    // Invalidate in-flight searches immediately, before the debounce fires.
    version++;
    clearTimeout(debounce);
    debounce = setTimeout(() => void load({ reset: true }), 250);
  });
  $("history-status").addEventListener("change", () => void load({ reset: true }));
  $("history-refresh").addEventListener("click", () => void load());
  $("history-previous").addEventListener("click", () => { offset = Math.max(0, offset - limit); void load(); });
  $("history-next").addEventListener("click", () => { offset += limit; void load(); });
  return { load };
}
