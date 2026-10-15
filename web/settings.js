import { $, request } from "./api.js";

export function createSettings() {
  let loaded = false;
  function retentionNote(settings) {
    $("history-retention").textContent = settings.retention_days == null
      ? "新しい翻訳は無期限に保存します。保存したPDFをここから取り出せます。"
      : `新しい翻訳は完了後${settings.retention_days}日間保存します。各PDFの期限は詳細画面で確認できます。`;
    $("retention-mode").value = settings.retention_days == null ? "forever" : "days";
    $("retention-days").value = settings.retention_days ?? 30;
    toggleDays();
  }
  function toggleDays() {
    $("retention-days-field").hidden = $("retention-mode").value !== "days";
    $("retention-days").disabled = $("retention-mode").value !== "days";
  }
  async function load() {
    $("settings-save").disabled = true;
    $("settings-message").textContent = "設定を読み込んでいます…";
    try {
      retentionNote(await request("/api/settings"));
      loaded = true;
      $("settings-message").textContent = "";
    } catch (error) {
      loaded = false;
      $("settings-message").textContent = error.message;
    } finally {
      $("settings-save").disabled = !loaded;
    }
  }
  $("retention-mode").addEventListener("change", toggleDays);
  $("settings-refresh").addEventListener("click", () => void load());
  $("settings-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!loaded || !$("settings-form").reportValidity()) return;
    $("settings-save").disabled = true;
    $("settings-message").textContent = "保存しています…";
    try {
      const retention_days = $("retention-mode").value === "forever" ? null : Number($("retention-days").value);
      retentionNote(await request("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days }),
      }));
      $("settings-message").textContent = "保存しました。今後完了する翻訳に適用します。";
    } catch (error) {
      $("settings-message").textContent = error.message;
    } finally {
      $("settings-save").disabled = false;
    }
  });
  return { load };
}
