import { $, request, createSubmissionKey } from "./api.js";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

export function createReview(onCreated, showPage) {
  let jobId = null, version = 0, data = null, busy = false, dirty = false, key = null;
  const cards = new Map();
  const message = (text, error = false) => {
    $("review-message").textContent = text;
    $("review-message").classList.toggle("is-error", error);
  };
  const decisions = () => [...cards].map(([id, card]) => ({
    id, action: card.select.value, text: card.select.value === "edit" ? card.input.value : null,
  }));
  function controls() {
    const editable = cards.size > 0;
    $("review-save").hidden = $("review-apply").hidden = !editable;
    $("review-save").disabled = busy || !dirty;
    $("review-apply").disabled = busy || !decisions().some(item => item.action !== "pending");
    $("review-save").textContent = busy ? "処理しています…" : "下書きを保存";
    $("review-apply").textContent = busy ? "処理しています…" : "確認内容をPDFに反映";
    $("review-reload").disabled = $("review-prepare").disabled = busy;
    for (const card of cards.values()) {
      card.select.disabled = busy;
      card.input.disabled = busy;
    }
  }
  function changed() {
    dirty = true;
    key = null;
    message("未保存の変更があります。下書きを保存すると、履歴から続きを編集できます。");
    controls();
  }
  function card(item, index) {
    const article = node("article", "review-card");
    article.dataset.reviewId = item.id;
    const heading = node("div", "section-heading");
    heading.append(node("h3", "", `${index + 1}. ${item.page ? `原文 ${item.page}ページ` : "文書全体"} · ${item.block_id || item.stage}`));
    if (item.page) {
      const open = node("button", "text-button", "このページを表示 ↑");
      open.type = "button";
      open.addEventListener("click", () => {
        void showPage(item.page);
        $("preview-status").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      heading.append(open);
    }
    article.append(heading, node("p", "review-warning", item.message));
    if (item.applied != null && item.decision.action !== "pending")
      article.append(node("p", item.applied ? "review-applied" : "review-warning",
        item.applied ? "確認内容を反映済みです。引き続き修正できます。" : "この修正は安全に配置できず、原文を保持しました。訳文はPDF末尾にも残しています。"));
    if (!item.editable) {
      article.append(node("p", "muted", "この注意はブロックの本文編集では解消できません。PDF・OCR・サーバーの設定などを確認してください。"));
      return article;
    }
    const columns = node("div", "review-columns");
    const source = node("div");
    source.append(node("h4", "field-label", "原文"), node("pre", "review-text", item.source));
    const candidate = node("div");
    candidate.append(node("h4", "field-label", "翻訳候補"), node("pre", "review-text", item.candidate ?? "候補を取得できませんでした。手入力で修正できます。"));
    if (item.candidate != null && !item.candidate_validated)
      candidate.append(node("p", "review-warning", item.candidate_complete
        ? "URL・コードの検証を通過していない候補です。欠落・変更を確認してから採用してください。"
        : "途中までの候補です。そのまま採用できません。手入力で全文を補ってください。"));
    columns.append(source, candidate);
    article.append(columns);
    if (item.literals.length) {
      const details = node("details", "review-literals");
      details.append(node("summary", "", "原文のURL・コード・リンク先を確認"));
      for (const literal of item.literals)
        details.append(node("pre", "review-text", literal.href && literal.href !== literal.text
          ? `${literal.text}\nリンク先：${literal.href}` : literal.text));
      article.append(details);
    }
    const label = node("label", "field-label", "採用方針");
    const select = node("select", "review-action");
    select.id = `review-action-${index}`;
    label.htmlFor = select.id;
    for (const [value, text] of [["pending", "未確認（自動処理の結果を使う）"], ["original", "原文を採用"],
      ["adopt", "翻訳候補を採用"], ["edit", "手入力で修正"]]) {
      const option = node("option", "", text);
      option.value = value;
      option.disabled = value === "adopt" && !item.can_adopt;
      select.append(option);
    }
    select.value = item.decision.action;
    const editLabel = node("label", "field-label", "修正後の本文（URLは正しい文字列で入力）");
    const input = node("textarea", "review-input");
    input.id = `review-text-${index}`;
    editLabel.htmlFor = input.id;
    input.rows = 5;
    input.maxLength = 20000;
    input.value = item.decision.text ?? item.candidate ?? item.source;
    input.spellcheck = false;
    const editor = node("div", "review-editor");
    editor.hidden = select.value !== "edit";
    editor.append(editLabel, input, node("p", "field-note", "URLの文字列を変更すると、クリック先にも反映します。枠に収まらない修正は原文を残し、PDF末尾に記録します。"));
    select.addEventListener("change", () => { editor.hidden = select.value !== "edit"; changed(); });
    input.addEventListener("input", changed);
    cards.set(item.id, { select, input });
    article.append(label, select, editor);
    return article;
  }
  async function load() {
    if (!jobId) return;
    const current = ++version, id = jobId;
    busy = true;
    data = null;
    cards.clear();
    $("review-list").replaceChildren();
    $("review-prepare").hidden = true;
    $("review-panel").hidden = false;
    $("review-panel").setAttribute("aria-busy", "true");
    message("注意箇所を読み込んでいます…");
    controls();
    try {
      const response = await request(`/api/jobs/${id}/review`);
      if (current !== version) return;
      data = response;
      dirty = false;
      key = null;
      $("review-prepare").hidden = response.available;
      $("review-count").textContent = response.available ? `${response.items.length}箇所` : "以前の履歴";
      if (!response.available) message(response.message);
      else {
        $("review-list").replaceChildren(...response.items.map(card));
        message(response.items.length ? "採用方針を選び、必要に応じて本文を修正してください。元の履歴を残して修正版を作成します。" : "確認が必要な注意箇所はありません。");
        $("review-panel").hidden = !response.items.length;
      }
    } catch (error) {
      if (current === version) message(error.message, true);
    } finally {
      if (current === version) {
        busy = false;
        $("review-panel").setAttribute("aria-busy", "false");
        controls();
      }
    }
  }
  async function save() {
    const payload = { revision: data.revision, decisions: decisions() };
    const saved = await request(`/api/jobs/${jobId}/review`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    data.revision = saved.revision;
    dirty = false;
    message("下書きを保存しました。履歴からいつでも続きを編集できます。");
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    const current = version;
    controls();
    try { await action(); }
    catch (error) { if (current === version) message(error.message, true); }
    finally { if (current === version) { busy = false; controls(); } }
  }
  $("review-save").addEventListener("click", () => void run(save));
  $("review-apply").addEventListener("click", () => void run(async () => {
    if (dirty) await save();
    key ||= createSubmissionKey();
    const job = await request(`/api/jobs/${jobId}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: data.revision, submission_key: key }),
    });
    onCreated(job);
  }));
  $("review-prepare").addEventListener("click", () => void run(async () => {
    const job = await request(`/api/jobs/${jobId}/review/prepare`, { method: "POST" });
    onCreated(job);
  }));
  $("review-reload").addEventListener("click", () => {
    if (dirty && !window.confirm("未保存の変更を破棄して、保存済みの下書きを読み直しますか？")) return;
    void load();
  });
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  return {
    canLeave() {
      if (busy) { message("保存・受付の処理が終わってから別のPDFを開いてください。"); return false; }
      return !dirty || window.confirm("下書きが未保存です。変更を破棄して別のPDFを開きますか？");
    },
    render(job) {
      if (job.status !== "succeeded") { this.clear(); return; }
      if (jobId === job.id) return;
      jobId = job.id;
      void load();
    },
    clear() {
      version++;
      jobId = data = null;
      dirty = busy = false;
      cards.clear();
      $("review-panel").hidden = true;
    },
  };
}
