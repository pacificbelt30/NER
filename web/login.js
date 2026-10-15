import { $, request } from "./api.js";

function destination() {
  try {
    const next = new URLSearchParams(location.search).get("next") || "/";
    const target = new URL(next, location.origin);
    return target.origin === location.origin && target.pathname !== "/login"
      ? target.pathname + target.search + (target.hash || location.hash) : "/";
  } catch {
    return "/";
  }
}

async function checkSession() {
  try {
    const session = await request("/api/auth/session");
    if (session.authenticated) { location.replace(destination()); return; }
    $("login-setup").hidden = session.configured;
    $("login-submit").disabled = !session.configured;
    $("login-status").textContent = "";
  } catch (error) {
    $("login-status").textContent = error.message;
  }
}

$("login-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!$("login-form").reportValidity()) return;
  $("login-submit").disabled = true;
  $("login-error").hidden = true;
  $("login-status").textContent = "ログインしています…";
  try {
    await request("/api/auth/login", { method: "POST", body: new FormData($("login-form")) });
    $("login-password").value = "";
    location.replace(destination());
  } catch (error) {
    $("login-error").textContent = error.message;
    $("login-error").hidden = false;
    $("login-status").textContent = "";
    $("login-submit").disabled = false;
    $("login-password").focus();
  }
});
void checkSession();
