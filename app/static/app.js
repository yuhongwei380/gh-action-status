const state = { snapshot: null, settings: null, authenticated: false, selectedLabels: new Set(), ignoredRunnerIds: new Set(), refreshTimer: null, toastTimer: null };
const $ = (selector) => document.querySelector(selector);
const themeStorageKey = "runner-beacon-theme";

function preferredTheme() {
  const saved = localStorage.getItem(themeStorageKey);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === "light" ? "#ffffff" : "#080b09";
  const button = $("#theme-button");
  if (button) {
    const nextTheme = theme === "light" ? "深色" : "浅色";
    button.setAttribute("aria-label", `切换为${nextTheme}主题`);
    button.title = `切换为${nextTheme}主题`;
  }
}

function setTheme(theme) {
  localStorage.setItem(themeStorageKey, theme);
  applyTheme(theme);
}

function enhanceSelect(select) {
  if (select.closest(".custom-select")) return;
  const wrapper = document.createElement("div");
  wrapper.className = "custom-select";
  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(select);
  select.classList.add("custom-select-native");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const controlLabel = select.getAttribute("aria-label") || select.previousElementSibling?.textContent?.trim() || "选择选项";
  trigger.setAttribute("aria-label", controlLabel);
  const value = document.createElement("span");
  const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  chevron.setAttribute("viewBox", "0 0 20 20");
  chevron.setAttribute("aria-hidden", "true");
  chevron.innerHTML = '<path d="m6 8 4 4 4-4"/>';
  trigger.append(value, chevron);

  const menu = document.createElement("div");
  menu.className = "select-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  const optionButtons = [...select.options].map((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "select-option";
    button.setAttribute("role", "option");
    button.dataset.value = option.value;
    button.textContent = option.textContent;
    menu.append(button);
    return button;
  });
  wrapper.append(trigger, menu);

  function sync() {
    const selected = select.options[select.selectedIndex];
    value.textContent = selected?.textContent || "请选择";
    optionButtons.forEach((button) => button.setAttribute("aria-selected", String(button.dataset.value === select.value)));
  }
  function close(restoreFocus = false) {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  }
  function open(focusIndex = null) {
    document.querySelectorAll(".select-menu:not([hidden])").forEach((other) => {
      if (other !== menu) {
        other.hidden = true;
        other.parentElement.querySelector(".select-trigger")?.setAttribute("aria-expanded", "false");
      }
    });
    const triggerRect = trigger.getBoundingClientRect();
    const contentRect = trigger.closest(".settings-content")?.getBoundingClientRect();
    const estimatedHeight = Math.min(260, optionButtons.length * 38 + 12);
    const spaceBelow = (contentRect?.bottom ?? window.innerHeight) - triggerRect.bottom - 16;
    const spaceAbove = triggerRect.top - (contentRect?.top ?? 0) - 16;
    const openUp = spaceBelow < estimatedHeight && spaceAbove > spaceBelow;
    menu.classList.toggle("open-up", openUp);
    menu.style.maxHeight = `${Math.max(38, Math.min(260, openUp ? spaceAbove : spaceBelow))}px`;
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusIndex !== null) {
      const selectedIndex = Math.max(0, optionButtons.findIndex((button) => button.dataset.value === select.value));
      optionButtons[focusIndex === "last" ? optionButtons.length - 1 : selectedIndex]?.focus();
    }
  }
  trigger.addEventListener("click", () => menu.hidden ? open() : close());
  trigger.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      open(event.key === "ArrowUp" || event.key === "End" ? "last" : "selected");
    }
  });
  menu.addEventListener("click", (event) => {
    const button = event.target.closest(".select-option");
    if (!button) return;
    select.value = button.dataset.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    sync(); close(true);
  });
  menu.addEventListener("keydown", (event) => {
    const current = optionButtons.indexOf(document.activeElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      let next = event.key === "Home" ? 0 : event.key === "End" ? optionButtons.length - 1 : current + (event.key === "ArrowDown" ? 1 : -1);
      next = (next + optionButtons.length) % optionButtons.length;
      optionButtons[next]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault(); close(true);
    } else if (event.key === "Tab") {
      close();
    }
  });
  document.addEventListener("click", (event) => { if (!wrapper.contains(event.target)) close(); });
  select.addEventListener("change", sync);
  select.customSelectSync = sync;
  sync();
}

function enhanceAllSelects() {
  document.querySelectorAll("select").forEach(enhanceSelect);
}

function syncCustomSelects() {
  document.querySelectorAll("select").forEach((select) => select.customSelectSync?.());
}

applyTheme(preferredTheme());

async function api(path, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* empty response */ }
    if (response.status === 401 && path !== "/api/login" && payload.code !== "github_api_error") showLogin();
    if (!response.ok) throw new Error(payload.detail || "请求失败");
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("服务响应超时，请检查容器状态或 GitHub 网络连接");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function showLogin() {
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
  setTimeout(() => $("#login-password").focus(), 0);
}

function showApp(insecureDefaults) {
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#security-warning").hidden = !insecureDefaults || !state.authenticated;
}

async function initialize() {
  try {
    const session = await api("/api/session");
    state.authenticated = session.authenticated;
    showApp(session.insecure_defaults);
    await loadRunners();
  } catch (error) {
    state.authenticated = false;
    showApp(false);
    showError(error.message);
  }
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  const settings = state.settings;
  $("#scope-type").value = settings.scope_type;
  $("#scope-input").value = settings.scope;
  $("#api-url").value = settings.api_url;
  $("#refresh-interval").value = String(settings.refresh_interval);
  $("#token-input").value = "";
  $("#token-input").required = !settings.has_token;
  $("#token-hint").textContent = settings.has_token
    ? `已保存 Token ${settings.token_hint}；留空表示不修改`
    : "Token 只会发送到本服务后端";
  const alerts = settings.alerts || {};
  $("#alerts-enabled").checked = Boolean(alerts.enabled);
  $("#dingtalk-webhook").value = alerts.webhook || "";
  $("#dingtalk-secret").value = "";
  $("#offline-after").value = String(alerts.offline_after || 120);
  $("#recovery-enabled").checked = alerts.recovery_enabled !== false;
  $("#default-mentions").value = (alerts.default_mentions || []).join(", ");
  $("#webhook-hint").textContent = alerts.has_webhook
    ? "Webhook 已在后端加密持久化保存，可直接查看或修改"
    : "Webhook 会在后端加密持久化保存";
  $("#secret-hint").textContent = alerts.has_secret
    ? "已保存加签密钥；留空表示不修改"
    : "机器人启用“加签”时填写";
  renderAlertRoutes(alerts.routes || []);
  state.ignoredRunnerIds = new Set(alerts.ignored_runner_ids || []);
  $("#ignored-runner-search").value = "";
  renderIgnoredRunners();
  updateScopeHelp();
  syncCustomSelects();
}

function parseMentions(value) {
  return [...new Set(value.split(/[，,;；\s]+/).map((item) => item.trim()).filter(Boolean))];
}

function selectSettingsTab(name) {
  document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
    const selected = tab.dataset.settingsTab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
  });
  closeIgnoredRunnerMenu();
  $(".settings-content").scrollTop = 0;
}

function closeIgnoredRunnerMenu(restoreFocus = false) {
  $("#ignored-runner-menu").hidden = true;
  $("#ignored-runner-button").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#ignored-runner-button").focus();
}

function renderIgnoredRunners() {
  const scope = state.snapshot?.scope;
  const matchesScope = scope?.type === state.settings?.scope_type && scope?.name === state.settings?.scope;
  const runners = new Map((matchesScope ? state.snapshot.runners : []).map((runner) => [String(runner.id), runner.name]));
  // Keep saved selections removable even when GitHub no longer returns them.
  state.ignoredRunnerIds.forEach((id) => {
    if (!runners.has(id)) runners.set(id, `Runner #${id}（当前列表中不可用）`);
  });
  const query = $("#ignored-runner-search").value.trim().toLowerCase();
  const options = $("#ignored-runner-options");
  options.replaceChildren();
  [...runners].sort((a, b) => a[1].localeCompare(b[1])).forEach(([id, name]) => {
    if (query && !`${name} ${id}`.toLowerCase().includes(query)) return;
    const label = document.createElement("label");
    label.className = "check-control ignored-runner-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = id;
    input.checked = state.ignoredRunnerIds.has(id);
    input.addEventListener("change", () => {
      if (input.checked) state.ignoredRunnerIds.add(id);
      else state.ignoredRunnerIds.delete(id);
      renderIgnoredRunnerSelection(runners);
    });
    const text = document.createElement("span");
    text.textContent = name;
    label.append(input, text);
    options.append(label);
  });
  $("#ignored-runner-empty").hidden = options.children.length > 0;
  $("#ignored-runner-empty").textContent = runners.size
    ? "没有匹配的 Runner。"
    : "暂无 Runner，请先保存连接设置并成功获取机器列表。";
  renderIgnoredRunnerSelection(runners);
}

function renderIgnoredRunnerSelection(runners) {
  const count = state.ignoredRunnerIds.size;
  $("#ignored-runner-summary").textContent = count ? `已选择 ${count} 台 Runner` : "选择要忽略的 Runner（可多选）";
  $("#ignored-runner-count").textContent = count ? `已忽略 ${count} 台 Runner` : "未忽略任何 Runner";
  $("#clear-ignored-runners").hidden = !count;
  const chips = $("#ignored-runner-chips");
  chips.replaceChildren();
  state.ignoredRunnerIds.forEach((id) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ignored-runner-chip";
    const name = runners.get(id) || `Runner #${id}`;
    chip.textContent = `${name} ×`;
    chip.setAttribute("aria-label", `取消忽略 ${name}`);
    chip.addEventListener("click", () => {
      state.ignoredRunnerIds.delete(id);
      renderIgnoredRunners();
    });
    chips.append(chip);
  });
}

function createRouteRow(route = {}) {
  const row = document.createElement("div");
  row.className = "alert-route";
  row.innerHTML = `
    <div class="field-control route-type">
      <label>匹配方式</label>
      <select class="route-match-type" aria-label="映射规则匹配方式">
        <option value="runner">Runner 名称</option>
        <option value="label">Label</option>
      </select>
    </div>
    <label class="route-value-label"><span>匹配值</span>
      <input class="route-match-value" placeholder="Runner 名称（精确匹配）" maxlength="200">
    </label>
    <label class="route-mentions-label"><span>@ 手机号</span>
      <input class="route-mentions" inputmode="tel" placeholder="13800138000, 13900139000">
    </label>
    <button class="route-remove" type="button" aria-label="删除此映射规则" title="删除规则">×</button>`;
  const type = row.querySelector(".route-match-type");
  const value = row.querySelector(".route-match-value");
  type.value = route.match_type || "runner";
  value.value = route.match_value || "";
  row.querySelector(".route-mentions").value = (route.mentions || []).join(", ");
  const updatePlaceholder = () => {
    value.placeholder = type.value === "runner" ? "Runner 名称（精确匹配）" : "Label 名称（精确匹配）";
  };
  type.addEventListener("change", updatePlaceholder);
  row.querySelector(".route-remove").addEventListener("click", () => {
    row.remove();
    updateRoutesEmpty();
  });
  updatePlaceholder();
  $("#alert-routes").append(row);
  enhanceSelect(type);
  updateRoutesEmpty();
}

function renderAlertRoutes(routes) {
  $("#alert-routes").replaceChildren();
  routes.forEach(createRouteRow);
  updateRoutesEmpty();
}

function updateRoutesEmpty() {
  $("#routes-empty").hidden = Boolean($("#alert-routes").children.length);
}

function collectAlertRoutes() {
  return [...document.querySelectorAll(".alert-route")].map((row) => ({
    match_type: row.querySelector(".route-match-type").value,
    match_value: row.querySelector(".route-match-value").value.trim(),
    mentions: parseMentions(row.querySelector(".route-mentions").value),
  })).filter((route) => route.match_value);
}

async function loadRunners(force = false) {
  setSyncState("loading");
  $("#refresh-button").classList.add("spinning");
  if (!state.snapshot) {
    $("#loading-state").hidden = false;
    $("#dashboard").hidden = true;
    $("#error-state").hidden = true;
  }
  try {
    const snapshot = await api(`/api/runners${force ? "?force=true" : ""}`, {}, 20000);
    state.snapshot = snapshot;
    if (state.settings) renderIgnoredRunners();
    renderDashboard();
    scheduleRefresh(snapshot.refresh_interval);
    setSyncState("online");
  } catch (error) {
    setSyncState("error");
    if (!state.snapshot) showError(error.message);
    else toast(`刷新失败：${error.message}`);
  } finally {
    $("#refresh-button").classList.remove("spinning");
    $("#loading-state").hidden = true;
  }
}

function scheduleRefresh(seconds) {
  clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(() => loadRunners(false), seconds * 1000);
}

function setSyncState(kind) {
  const element = $("#connection-state");
  element.className = `connection-state ${kind === "online" ? "online" : kind === "error" ? "error" : ""}`;
  element.querySelector("span").textContent = kind === "online" ? "已连接" : kind === "error" ? "同步异常" : "正在同步";
}

function showError(message) {
  $("#dashboard").hidden = true;
  $("#error-state").hidden = false;
  $("#error-message").textContent = message;
  $("#error-action").textContent = message.includes("配置") ? "开始配置" : "检查配置";
}

function renderDashboard() {
  const { summary, scope, runners, fetched_at, rate_limit } = state.snapshot;
  $("#error-state").hidden = true;
  $("#dashboard").hidden = false;
  $("#scope-name").textContent = `${scopeTypeName(scope.type)} · ${scope.name}`;
  $("#online-count").textContent = summary.online;
  $("#total-count").textContent = summary.total;
  $("#idle-count").textContent = summary.idle;
  $("#busy-count").textContent = summary.busy;
  $("#offline-count").textContent = summary.offline;
  $("#utilization-count").textContent = summary.utilization;
  renderSignalLane(runners);
  renderLabelFilter(state.snapshot.labels);
  renderRunners();
  const fetched = new Date(fetched_at);
  $("#last-updated").textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(fetched)}${state.snapshot.cached ? " · 缓存" : ""}`;
  $("#rate-limit").textContent = rate_limit.remaining == null ? "" : `API 配额 ${rate_limit.remaining} / ${rate_limit.limit}`;
}

function renderSignalLane(runners) {
  const lane = $("#signal-lane");
  lane.replaceChildren();
  if (!runners.length) {
    const item = document.createElement("i"); item.className = "offline"; lane.append(item); return;
  }
  runners.forEach((runner) => {
    const item = document.createElement("i");
    item.className = runner.status === "offline" ? "offline" : runner.busy ? "busy" : "idle";
    item.title = `${runner.name} · ${statusText(runner)}`;
    lane.append(item);
  });
}

function renderRunners() {
  if (!state.snapshot) return;
  const search = $("#runner-search").value.trim().toLowerCase();
  const status = $("#status-filter").value;
  const filtered = state.snapshot.runners.filter((runner) => {
    const haystack = `${runner.name} ${runner.os} ${runner.labels.join(" ")}`.toLowerCase();
    const runnerState = runner.status === "offline" ? "offline" : runner.busy ? "busy" : "idle";
    const matchesLabels = [...state.selectedLabels].every((label) => runner.labels.includes(label));
    return (!search || haystack.includes(search)) && (status === "all" || status === runnerState) && matchesLabels;
  });
  const body = $("#runner-rows"); body.replaceChildren();
  const fragment = document.createDocumentFragment();
  filtered.forEach((runner) => {
    const row = document.createElement("tr");
    const kind = runner.status === "offline" ? "offline" : runner.busy ? "busy" : "idle";
    row.className = `runner-row ${kind}`;
    row.innerHTML = `<td class="runner-name-cell"><div class="runner-name"></div></td><td class="labels-cell"><div class="label-chips"></div></td><td class="status-cell"><span class="status-pill ${kind}"><i></i>${statusText(runner)}</span></td><td class="os-cell"></td><td class="job-cell"><span class="job-state ${kind}">${kind === "busy" ? "作业执行中" : kind === "idle" ? "可接收作业" : "不可用"}</span></td>`;
    row.querySelector(".runner-name").textContent = runner.name;
    row.querySelector(".os-cell").textContent = runner.os;
    const chips = row.querySelector(".label-chips");
    runner.labels.forEach((label) => { const chip = document.createElement("span"); chip.className = "label-chip"; chip.textContent = label; chips.append(chip); });
    fragment.append(row);
  });
  body.append(fragment);
  $("#runner-empty").hidden = filtered.length > 0;
}

function renderLabelFilter(labels) {
  const validNames = new Set(labels.map((label) => label.name));
  state.selectedLabels.forEach((name) => { if (!validNames.has(name)) state.selectedLabels.delete(name); });
  const options = $("#label-filter-options");
  options.replaceChildren();
  labels.forEach((label) => {
    const option = document.createElement("label");
    option.className = "label-filter-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = label.name;
    checkbox.checked = state.selectedLabels.has(label.name);
    const name = document.createElement("span");
    name.textContent = label.name;
    name.title = label.name;
    const count = document.createElement("small");
    count.textContent = String(label.total);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedLabels.add(label.name);
      else state.selectedLabels.delete(label.name);
      updateLabelFilterText();
      renderRunners();
    });
    option.append(checkbox, name, count);
    options.append(option);
  });
  $("#label-filter-button").disabled = labels.length === 0;
  updateLabelFilterText();
}

function updateLabelFilterText() {
  const selected = [...state.selectedLabels];
  $("#label-filter-text").textContent = selected.length === 0 ? "全部 Labels" : selected.length === 1 ? selected[0] : `已选 ${selected.length} 个 Labels`;
  $("#clear-label-filter").disabled = selected.length === 0;
}

function closeLabelFilter(restoreFocus = false) {
  $("#label-filter-menu").hidden = true;
  $("#label-filter-button").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#label-filter-button").focus();
}

function toggleLabelFilter() {
  const menu = $("#label-filter-menu");
  const willOpen = menu.hidden;
  menu.hidden = !willOpen;
  $("#label-filter-button").setAttribute("aria-expanded", String(willOpen));
  if (willOpen) menu.querySelector("input")?.focus();
}

function statusText(runner) { return runner.status === "offline" ? "离线" : runner.busy ? "执行中" : "空闲"; }
function scopeTypeName(type) { return ({ organization: "组织", repository: "仓库", enterprise: "企业" })[type] || type; }

function updateScopeHelp() {
  const type = $("#scope-type").value;
  const labels = { organization: ["组织名称", "例如 openai"], repository: ["仓库", "例如 owner/repository"], enterprise: ["企业 slug", "例如 acme-enterprise"] };
  $("#scope-label").textContent = labels[type][0];
  $("#scope-input").placeholder = labels[type][1];
}

async function openSettings() {
  try {
    const session = await api("/api/session");
    state.authenticated = session.authenticated;
    if (!session.authenticated) return showLogin();
    await loadSettings();
  } catch (_) { return; }
  $("#settings-error").textContent = "";
  selectSettingsTab("connection");
  $("#settings-dialog").showModal();
}

function toast(message) {
  const element = $("#toast"); element.textContent = message; element.hidden = false;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { element.hidden = true; }, 3200);
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true; $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("#login-password").value }) });
    $("#login-password").value = "";
    const session = await api("/api/session");
    state.authenticated = true;
    showApp(session.insecure_defaults);
    await openSettings();
  } catch (error) { $("#login-error").textContent = error.message; } finally { button.disabled = false; }
});
$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const invalid = event.currentTarget.querySelector(":invalid");
  if (invalid) {
    selectSettingsTab(invalid.closest('[role="tabpanel"]').id.replace("-panel", ""));
    invalid.reportValidity();
    return;
  }
  const button = event.submitter; button.disabled = true; $("#settings-error").textContent = "";
  const payload = {
    scope_type: $("#scope-type").value,
    scope: $("#scope-input").value,
    api_url: $("#api-url").value,
    token: $("#token-input").value || null,
    refresh_interval: Number($("#refresh-interval").value),
    alerts: {
      enabled: $("#alerts-enabled").checked,
      webhook: $("#dingtalk-webhook").value || null,
      secret: $("#dingtalk-secret").value || null,
      offline_after: Number($("#offline-after").value),
      recovery_enabled: $("#recovery-enabled").checked,
      default_mentions: parseMentions($("#default-mentions").value),
      routes: collectAlertRoutes(),
      ignored_runner_ids: [...state.ignoredRunnerIds],
    },
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    $("#settings-dialog").close();
    state.snapshot = null;
    toast("连接配置已保存，正在测试 GitHub 连接");
    await loadSettings();
    loadRunners(true);
  } catch (error) { $("#settings-error").textContent = error.message; } finally { button.disabled = false; }
});
$(".settings-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-settings-tab]");
  if (tab) selectSettingsTab(tab.dataset.settingsTab);
});
$(".settings-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll("[data-settings-tab]")];
  const current = tabs.indexOf(document.activeElement);
  const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  selectSettingsTab(tabs[index].dataset.settingsTab);
  tabs[index].focus();
});
$("#ignored-runner-button").addEventListener("click", () => {
  const menu = $("#ignored-runner-menu");
  if (!menu.hidden) return closeIgnoredRunnerMenu();
  renderIgnoredRunners();
  menu.hidden = false;
  $("#ignored-runner-button").setAttribute("aria-expanded", "true");
  $("#ignored-runner-search").focus();
});
$("#ignored-runner-search").addEventListener("input", renderIgnoredRunners);
$("#ignored-runner-picker").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#ignored-runner-menu").hidden) {
    event.preventDefault();
    event.stopPropagation();
    closeIgnoredRunnerMenu(true);
  }
});
document.addEventListener("click", (event) => {
  if (!$("#ignored-runner-picker").contains(event.target)) closeIgnoredRunnerMenu();
});
$("#clear-ignored-runners").addEventListener("click", () => {
  state.ignoredRunnerIds.clear();
  renderIgnoredRunners();
});
$("#add-route-button").addEventListener("click", () => createRouteRow());
$("#test-alert-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("#settings-error").textContent = "";
  try {
    await api("/api/alerts/test", { method: "POST" }, 15000);
    toast("钉钉测试通知已发送");
  } catch (error) {
    $("#settings-error").textContent = `${error.message}（请先保存当前钉钉配置）`;
  } finally {
    button.disabled = false;
  }
});
$("#refresh-button").addEventListener("click", () => loadRunners(true));
$("#theme-button").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));
$("#settings-button").addEventListener("click", openSettings);
$("#error-action").addEventListener("click", openSettings);
$("#close-settings").addEventListener("click", () => $("#settings-dialog").close());
$("#cancel-settings").addEventListener("click", () => $("#settings-dialog").close());
$("#scope-type").addEventListener("change", updateScopeHelp);
$("#runner-search").addEventListener("input", renderRunners);
$("#status-filter").addEventListener("change", renderRunners);
$("#label-filter-button").addEventListener("click", toggleLabelFilter);
$("#clear-label-filter").addEventListener("click", () => {
  state.selectedLabels.clear();
  $("#label-filter-options").querySelectorAll("input").forEach((input) => { input.checked = false; });
  updateLabelFilterText();
  renderRunners();
});
$("#label-filter-menu").addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); closeLabelFilter(true); } });
document.addEventListener("click", (event) => { if (!$("#label-filter").contains(event.target)) closeLabelFilter(); });
$("#public-view-button").addEventListener("click", () => {
  showApp(false);
  if (!state.snapshot) loadRunners();
  else scheduleRefresh(state.snapshot.refresh_interval);
});
$("#logout-button").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  state.authenticated = false;
  $("#settings-dialog").close();
  showApp(false);
  toast("已退出管理员设置，公开状态页仍可查看");
});
$("#settings-dialog").addEventListener("click", (event) => { if (event.target === $("#settings-dialog")) $("#settings-dialog").close(); });

enhanceAllSelects();
initialize();
