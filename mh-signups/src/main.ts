import "./styles.css";
import { branding } from "./branding.ts";
import { Controller, type AppState } from "./controller.ts";
import { buildView } from "./view-model.ts";

const root = document.getElementById("app")!;

const controller = new Controller({
  fetch: (input, init) => fetch(input, init),
  now: () => Date.now(),
  setInterval: (fn, ms) => window.setInterval(fn, ms),
  clearInterval: (h) => window.clearInterval(h as number),
  isVisible: () => document.visibilityState === "visible",
});

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function header(): HTMLElement {
  const h = el("header", "top");
  h.append(el("p", "firm", branding.firmLine), el("h1", "app-name", branding.appName));
  return h;
}

function renderLogin(state: AppState): void {
  root.replaceChildren();
  const main = el("main", "screen");
  const form = el("form", "login");
  form.setAttribute("autocomplete", "on");

  // Hidden username lets phone password managers save and fill the passphrase.
  const user = el("input");
  user.type = "text";
  user.name = "username";
  user.value = "mh-signups";
  user.autocomplete = "username";
  user.hidden = true;

  const label = el("label", "login-label", "Passphrase");
  const input = el("input", "login-input");
  input.type = "password";
  input.name = "passphrase";
  input.autocomplete = "current-password";
  input.required = true;
  input.id = "passphrase";
  label.htmlFor = "passphrase";

  const button = el("button", "login-button", "Sign in");
  button.type = "submit";

  const message = el("p", "login-message");
  message.setAttribute("role", "alert");
  if (state.loginError === "invalid") message.textContent = "That passphrase did not work.";
  else if (state.loginError === "throttled") message.textContent = `Too many attempts. Try again in ${Math.ceil(state.retryAfterSeconds / 60)} min.`;
  else if (state.loginError === "unconfigured") message.textContent = "Sign-in is not set up on the server.";
  else if (state.loginError === "network_error") message.textContent = "Could not reach the server.";

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    button.disabled = true;
    void controller.submitLogin(input.value).finally(() => {
      input.value = "";
    });
  });

  form.append(user, label, input, button, message);
  main.append(header(), form);
  root.append(main);
  if (state.loginError === null) input.focus();
}

function renderBlocked(): void {
  root.replaceChildren();
  const main = el("main", "screen");
  const msg = el("p", "blocked", "Sign-in is not configured on the server. Nothing is shown until it is.");
  msg.setAttribute("role", "alert");
  main.append(header(), msg);
  root.append(main);
}

let numbers: { value: HTMLElement; note: HTMLElement }[] = [];
let bannerEl: HTMLElement;
let demoEl: HTMLElement;
let footerText: HTMLElement;
let refreshBtn: HTMLButtonElement;
let built = false;

function buildScreen(): void {
  root.replaceChildren();
  const main = el("main", "screen");
  demoEl = el("p", "demo-flag", "DEMO DATA");
  demoEl.hidden = true;

  const metrics = el("section", "metrics");
  numbers = [];
  for (const key of ["today", "week", "month"]) {
    const block = el("div", `metric metric-${key}`);
    block.dataset.metric = key;
    const label = el("p", "metric-label");
    const value = el("p", "metric-value");
    const note = el("p", "metric-note");
    block.append(label, value, note);
    metrics.append(block);
    numbers.push({ value, note });
  }

  bannerEl = el("div", "banners");
  bannerEl.setAttribute("role", "status");
  bannerEl.setAttribute("aria-live", "polite");

  const footer = el("footer", "foot");
  footerText = el("p", "foot-text");
  const actions = el("div", "foot-actions");
  refreshBtn = el("button", "link", "Refresh view");
  refreshBtn.type = "button";
  refreshBtn.addEventListener("click", () => void controller.refreshView());
  const out = el("button", "link", "Sign out");
  out.type = "button";
  out.addEventListener("click", () => void controller.signOut());
  actions.append(refreshBtn, out);
  footer.append(footerText, actions);

  main.append(header(), demoEl, metrics, bannerEl, footer);
  root.append(main);
  built = true;
}

function renderReady(state: AppState): void {
  if (!built) buildScreen();
  const view = buildView({ phase: state.phase, stats: state.stats, offline: state.offline, now: new Date(state.nowMs) });
  demoEl.hidden = !view.demo;
  view.metrics.forEach((m, i) => {
    const n = numbers[i]!;
    const block = n.value.parentElement!;
    (block.firstElementChild as HTMLElement).textContent = m.label;
    n.value.textContent = m.value;
    n.value.setAttribute("aria-label", m.value === "--" ? "not available" : m.value);
    n.note.textContent = m.note ?? "";
    n.note.hidden = m.note === null;
  });
  bannerEl.replaceChildren(...view.banners.map((b) => el("p", "banner", b)));
  footerText.textContent = view.footer;
  refreshBtn.textContent = state.refreshing ? "Checking..." : "Refresh view";
  refreshBtn.disabled = state.refreshing;
}

function render(state: AppState): void {
  if (state.phase === "login") {
    built = false;
    renderLogin(state);
  } else if (state.phase === "auth_unconfigured") {
    built = false;
    renderBlocked();
  } else {
    renderReady(state);
  }
}

let lastPhase: AppState["phase"] | null = null;
let lastLoginError: AppState["loginError"] = null;
controller.subscribe((state) => {
  // Re-render the login form only when something about it changed, so typing is never interrupted.
  if (state.phase === "login" && lastPhase === "login" && state.loginError === lastLoginError) return;
  lastPhase = state.phase;
  lastLoginError = state.loginError;
  render(state);
});
render(controller.state);

document.addEventListener("visibilitychange", () => controller.onVisibilityChange());
window.addEventListener("pageshow", () => controller.onVisibilityChange());
window.addEventListener("online", () => controller.onOnline());
controller.start();
