import { $, request } from "./api.js";

export function createPreview(getJob, onRefresh, onError) {
  let pageNumber = 1;
  let version = 0;
  let expiryTimer;

  async function showPage(number) {
    const job = getJob();
    if (job?.status !== "succeeded") return;
    const pages = Math.max(1, job.result_summary?.source_pages || job.progress.pages);
    pageNumber = Math.max(1, Math.min(pages, number));
    const current = ++version;
    $("page-position").textContent = `${pageNumber} / ${pages} ページ`;
    $("previous-page").disabled = $("next-page").disabled = true;
    $("preview-status").textContent = "ページを読み込んでいます…";
    // Hide the preceding document until both new pages have loaded.
    $("original-frame").removeAttribute("src");
    $("translated-frame").removeAttribute("src");
    try {
      const images = await Promise.all(["original", "translated"].map(kind =>
        new Promise((resolve, reject) => {
          const image = new Image();
          image.onload = () => resolve({ kind, image });
          image.onerror = () => reject(new Error("ページを表示できませんでした。別タブで開くか、PDFを保存してください。"));
          image.src = `/api/jobs/${job.id}/preview/${kind}?page=${pageNumber}`;
        })));
      if (current !== version) return;
      for (const { kind, image } of images) {
        $(`${kind}-frame`).src = image.src;
        $(`${kind}-frame`).alt = `${kind === "original" ? "原文" : "日本語訳"} ${pageNumber}ページ`;
      }
      document.querySelectorAll(".page-scroll").forEach(pane => {
        pane.scrollTop = pane.scrollLeft = 0;
      });
      $("preview-status").textContent = "原文と日本語訳の同じページを表示しています。";
    } catch (error) {
      if (current === version) $("preview-status").textContent = error.message;
    } finally {
      if (current === version) {
        $("previous-page").disabled = pageNumber === 1;
        $("next-page").disabled = pageNumber === pages;
      }
    }
  }

  function render(job) {
    $("results").hidden = false;
    const untranslated = job.result_summary?.outcome === "untranslated";
    $("results-title").textContent = untranslated ? "原文とエラーレポート" : "翻訳結果";
    $("download-translated").textContent = untranslated ? "原文・エラーPDFを保存 ↓" : "訳文PDFを保存 ↓";
    $("download-translated").href = job.downloads.translated;
    $("download-comparison").hidden = !job.downloads.comparison;
    if (job.downloads.comparison) $("download-comparison").href = job.downloads.comparison;
    for (const kind of ["original", "translated"])
      $(`open-${kind}`).href = `${job.downloads[kind]}?inline=true`;
    if ($("results").dataset.job !== job.id) {
      $("results").dataset.job = job.id;
      void showPage(1);
    }
    $("expiry-note").textContent = job.expires_at
      ? `保存期限：${new Date(job.expires_at).toLocaleString("ja-JP")} · 履歴から再取得できます`
      : "保存期限なし · 履歴からいつでも再取得できます";
    clearTimeout(expiryTimer);
    if (job.expires_at) {
      // Browser timers overflow beyond about 24 days. Recheck before scheduling again.
      expiryTimer = setTimeout(async () => {
        try {
          const fresh = await request(`/api/jobs/${job.id}`);
          if (getJob()?.id === job.id) onRefresh(fresh);
        } catch (error) {
          if (getJob()?.id === job.id) onError(error);
        }
      }, Math.min(2147483647, Math.max(0, Date.parse(job.expires_at) - Date.now()) + 100));
    }
  }

  $("previous-page").addEventListener("click", () => void showPage(pageNumber - 1));
  $("next-page").addEventListener("click", () => void showPage(pageNumber + 1));
  $("preview-zoom").addEventListener("change", () => {
    document.querySelectorAll(".page-image").forEach(image => {
      image.style.width = `${$("preview-zoom").value}%`;
    });
  });
  return { render, showPage, clear() {
    version++;
    clearTimeout(expiryTimer);
    delete $("results").dataset.job;
  }};
}
