import { $, request } from "./api.js";

export function loginRequired() {
  location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
}

export async function initializeAuth() {
  window.addEventListener("ner:login-required", loginRequired);
  window.addEventListener("pageshow", event => {
    if (event.persisted) location.reload();
  });
  const session = await request("/api/auth/session");
  if (!session.authenticated) { loginRequired(); return false; }
  $("account-name").textContent = session.username;
  $("logout-button").addEventListener("click", async () => {
    $("logout-button").disabled = true;
    try {
      await request("/api/auth/logout", { method: "POST" });
      location.replace("/login");
    } catch (error) {
      $("account-message").textContent = error.message;
      $("account-message").hidden = false;
      $("logout-button").disabled = false;
    }
  });
  return true;
}
