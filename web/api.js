export const $ = (id) => document.getElementById(id);
export const ACTIVE = new Set(["queued", "running", "cancelling"]);

export function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  const value = bytes / (1024 * 1024);
  return `${Number.isInteger(value) ? value : value.toFixed(1)} MiB`;
}
export function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
export async function request(url, options = {}) {
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

