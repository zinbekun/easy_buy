const DAILY = "日用品";
const CATEGORIES = ["お菓子", "カップ麺", "飲み物", DAILY];
const DAILY_GROUPS = ["ティッシュ", "ウェットティッシュ", "紙コップ", "割りばし"];
const GOAL_DISPLAY_MS = 1800;
const REQUEST_INTERVAL_MS = 1500;
const STORAGE_KEY = "aeon-selected";
const COOPERATIVE_KEY = "aeon-cooperative";
const BAR_SHOW_SCROLL_UP_PX = 24;

const $ = (id) => document.getElementById(id);
const shiba = new Shiba($("shiba"));

let products = [];
let byId = new Map();
let previousIds = new Set();
let selected = loadSelection();
let category = CATEGORIES[0];
let sort = "price_asc";
let view = "list";
let rakuchin = null; // 楽チン機能の有効中だけ { previous: [{id, category, quantity}] } を持つ

function loadSelection() {
  try {
    return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY)) || []);
  } catch {
    return new Set();
  }
}

function saveSelection() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...selected]));
  } catch {
    // 保存できなくても画面の操作は続けられる
  }
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("ログインが必要です。");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `サーバーエラー（HTTP ${res.status}）`);
  return data;
}

const yen = (price) => `${price.toFixed(2)}円`;
const compareText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const byName = (a, b) => compareText(a.kana, b.kana);

function normalize(text) {
  const halfWidth = text.normalize("NFKC").toLowerCase();
  return halfWidth.replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
}

function compareContent(a, b, direction) {
  return a.content_group - b.content_group
    || direction * (a.content_value - b.content_value)
    || a.price - b.price
    || byName(a, b);
}

const SORTERS = {
  price_asc: (a, b) => a.price - b.price || byName(a, b),
  price_desc: (a, b) => b.price - a.price || byName(a, b),
  name: byName,
  content_desc: (a, b) => compareContent(a, b, -1),
  content_asc: (a, b) => compareContent(a, b, 1),
};

function isCrossing() {
  return $("search").value.trim() !== "" || $("prevFilter").checked;
}

function shownProducts() {
  const query = normalize($("search").value.trim());
  const prevOnly = $("prevFilter").checked;
  return products
    .filter((p) => isCrossing() || p.category === category)
    .filter((p) => !prevOnly || previousIds.has(p.id))
    .filter((p) => !query || normalize(p.name).includes(query) || normalize(p.kana).includes(query))
    .sort(SORTERS[sort]);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function photoBox(item) {
  const box = element("div", "card-photo");
  const fallback = () => element("div", "thumb-none", "画像なし");
  if (!item.image) {
    box.append(fallback());
    return box;
  }
  const img = element("img", "thumb");
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.alt = "";
  img.src = item.image;
  img.onerror = () => img.replaceWith(fallback());
  box.append(img);
  return box;
}

function priceBox(item) {
  const box = element("div", "card-price");
  if (item.base_price) box.append(element("div", "price-main", `${item.base_price.toLocaleString()}円`));
  const tax = element("div", "price-tax");
  tax.append(element("small", "", "税込 "), yen(item.price));
  box.append(tax);
  return box;
}

function itemCard(item, ...footer) {
  const card = element("article", "card");
  const name = element("div", "card-name");
  const link = element("a", "", item.name);
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  name.append(link);
  const meta = element("div", "card-meta", [item.category, item.content].filter(Boolean).join("・"));
  const foot = element("div", "card-foot");
  foot.append(...footer);
  card.append(photoBox(item), name, meta, priceBox(item), foot);
  return card;
}

function setSelected(ids, on) {
  ids.forEach((id) => (on ? selected.add(id) : selected.delete(id)));
  saveSelection();
  render();
}

function productCard(p) {
  const on = selected.has(p.id);
  const label = element("label", "select-btn");
  const box = element("input");
  box.type = "checkbox";
  box.checked = on;
  box.ariaLabel = `${p.name}を選択`;
  box.onchange = () => setSelected([p.id], box.checked);
  label.append(box, on ? "選択中" : "選択する");
  const card = itemCard(p, label);
  card.classList.toggle("selected", on);
  return card;
}

// 日用品はティッシュ・ウェットティッシュ・紙コップ・割りばしの種類ごとに見出しを付けて並べる
function dailyRows(items) {
  return DAILY_GROUPS.flatMap((group) => {
    const members = items.filter((p) => p.group === group);
    const heading = element("h3", "group-heading", `${group}（${members.length}件）`);
    return members.length
      ? [heading, ...members.map(productCard)]
      : [heading, element("p", "group-empty muted", "販売中の商品はありません。")];
  });
}

function renderList() {
  const items = shownProducts();
  const grouped = category === DAILY && !isCrossing();
  const rows = grouped ? dailyRows(items) : items.map(productCard);
  $("listBody").replaceChildren(...rows);
  $("listEmpty").hidden = items.length > 0;
  ["tabs", "bottomTabs"].forEach((id) => $(id).classList.toggle("crossing", isCrossing()));
  $("selCategory").disabled = isCrossing();
}

function rakuchinShortages() {
  const shortages = {};
  if (!rakuchin) return shortages;
  const chosen = selectedProducts();
  CATEGORIES.forEach((name) => {
    const before = rakuchin.previous.filter((item) => item.category === name).length;
    shortages[name] = Math.max(before - chosen.filter((p) => p.category === name).length, 0);
  });
  return shortages;
}

function rakuchinReady() {
  return Object.values(rakuchinShortages()).every((count) => count === 0);
}

// 前回もある商品は前回の個数、入れ替えた商品は同じジャンルで外れた商品の個数を順に使う
function rakuchinQuantities() {
  const chosen = selectedProducts();
  const chosenIds = new Set(chosen.map((p) => p.id));
  const before = new Map(rakuchin.previous.map((item) => [item.id, item.quantity]));
  const released = rakuchin.previous.filter((item) => !chosenIds.has(item.id));
  const quantities = {};
  chosen.forEach((p) => {
    if (before.has(p.id)) {
      quantities[p.id] = before.get(p.id);
    } else {
      const index = released.findIndex((item) => item.category === p.category);
      quantities[p.id] = index < 0 ? 1 : released.splice(index, 1)[0].quantity;
    }
  });
  return quantities;
}

function selectedProducts() {
  const order = (p) => CATEGORIES.indexOf(p.category);
  return [...selected]
    .map((id) => byId.get(id))
    .filter(Boolean)
    .sort((a, b) => order(a) - order(b) || a.price - b.price || byName(a, b));
}

function renderSelected() {
  const items = selectedProducts();
  const quantities = rakuchin ? rakuchinQuantities() : {};
  const rows = items.map((p) => {
    const button = element("button", "", "解除");
    button.onclick = () => setSelected([p.id], false);
    return rakuchin ? itemCard(p, element("div", "", `個数：${quantities[p.id]}個`), button) : itemCard(p, button);
  });
  $("selectedBody").replaceChildren(...rows);
  $("selectedEmpty").hidden = items.length > 0;
  $("confirmBtn").disabled = items.length === 0 || (Boolean(rakuchin) && !rakuchinReady());
  $("clearSelected").disabled = items.length === 0;
}

async function renderHistory() {
  const { items } = await api("/api/history");
  const rows = items.map((item) => itemCard(
    item,
    element("div", "", `購入回数：${item.count}回`),
    element("div", item.on_sale ? "" : "tag-off", item.on_sale ? "販売中" : "販売なし・対象外"),
  ));
  $("historyBody").replaceChildren(...rows);
  $("historyEmpty").hidden = items.length > 0;
}

function render() {
  const count = selected.size;
  $("selCount").textContent = `選択中：${count}商品`;
  $("navCount").textContent = count ? `（${count}）` : "";
  const chosen = selectedProducts();
  const shortages = rakuchinShortages();
  $("categoryCounts").replaceChildren(...CATEGORIES.map((name) => {
    const count = chosen.filter((p) => p.category === name).length;
    const badge = element("span", "category-count", `${name}${count}個`);
    if (shortages[name] > 0) {
      badge.classList.add("short");
      badge.append(element("span", "category-short", `-${shortages[name]}個`));
    }
    return badge;
  }));
  $("rakuchinGo").hidden = !rakuchin || view === "selected" || !rakuchinReady();
  $("rakuchinBtn").classList.toggle("active", Boolean(rakuchin));
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.textContent === category);
  });
  if (view === "list") renderList();
  if (view === "selected") renderSelected();
}

function showView(next) {
  view = next;
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("active", b.dataset.view === next));
  ["list", "selected", "history"].forEach((name) => { $(`view-${name}`).hidden = name !== next; });
  setBottomBar(false);
  if (next === "history") renderHistory();
  if (next === "selected") loadSettings();
  render();
  window.scrollTo(0, 0);
}

function notify(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

async function loadProducts() {
  const data = await api("/api/products");
  products = data.products;
  byId = new Map(products.map((p) => [p.id, p]));
  previousIds = new Set(data.previous_ids);
  const gone = [...selected].filter((id) => !byId.has(id));
  if (products.length) {
    gone.forEach((id) => selected.delete(id));
    saveSelection();
    if (gone.length) notify(`販売終了または対象外になった${gone.length}商品を選択から外しました。`);
  }
  $("fetchedAt").textContent = data.fetched_at ? `取得：${data.fetched_at}` : "";
  $("listNote").textContent = data.price_unknown
    ? `価格を確認できなかった${data.price_unknown}商品は表示していません。` : "";
  render();
  return data;
}

function showLoading(progress) {
  $("loading").hidden = false;
  const percent = Math.round(progress * 100);
  $("barFill").style.width = `${percent}%`;
  $("percent").textContent = `${percent}%`;
  shiba.setProgress(progress);
}

function showError(message) {
  shiba.stop();
  $("loadingMsg").textContent = "商品情報の取得に失敗しました";
  $("errorDetail").textContent = message;
  $("errorBox").hidden = false;
  $("closeError").hidden = products.length === 0;
  $("refreshBtn").disabled = false;
}

async function finishFetch() {
  showLoading(1);
  $("loadingMsg").textContent = "取得が完了しました";
  shiba.arrive();
  await loadProducts();
  setTimeout(() => {
    $("loading").hidden = true;
    shiba.stop();
    $("refreshBtn").disabled = false;
  }, GOAL_DISPLAY_MS);
}

async function crawl() {
  notify("");
  $("refreshBtn").disabled = true;
  $("errorBox").hidden = true;
  $("loadingMsg").textContent = "商品情報を取得中...";
  shiba.start();
  showLoading(0);
  const runId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    let step = { source: 0, page: 1 };
    while (step) {
      const result = await api("/api/crawl", { run_id: runId, ...step });
      showLoading(result.progress);
      step = result.next;
      if (step) await new Promise((resolve) => setTimeout(resolve, REQUEST_INTERVAL_MS));
    }
    await finishFetch();
  } catch (e) {
    showError(e.message);
  }
}

async function loadTabs(current) {
  $("sheetError").hidden = true;
  try {
    const { tabs } = await api("/api/sheets");
    const options = tabs.map((title) => new Option(title, title, false, title === current));
    $("sheetTab").replaceChildren(...options);
  } catch (e) {
    $("sheetTab").replaceChildren();
    showSheetError(e.message);
  }
}

function showSheetError(message) {
  $("sheetError").textContent = message;
  $("sheetError").hidden = false;
}

async function loadSettings() {
  const settings = await api("/api/settings");
  $("sheetUrl").value = settings.spreadsheet_url;
  $("saNote").textContent = settings.service_account_email
    ? `スプレッドシートを ${settings.service_account_email} に編集者として共有してください。`
    : "認証ファイル credentials/service_account.json がありません。README の手順で設定してください。";
  await loadTabs(settings.sheet_name);
}

async function transcribe() {
  try {
    const { count } = await api("/api/transcribe", {
      ids: selectedProducts().map((p) => p.id),
      sheet_name: $("sheetTab").value,
      quantities: rakuchin ? rakuchinQuantities() : {},
      cooperative: $("cooperative").checked,
    });
    rakuchin = null;
    previousIds = new Set(selectedProducts().map((p) => p.id));
    showView("list");
    notify(`${count}商品を「${$("sheetTab").value}」に転記しました。`);
  } catch (e) {
    showSheetError(e.message);
  }
}

function selectCategory(name) {
  category = name;
  $("search").value = "";
  $("prevFilter").checked = false;
  render();
  window.scrollTo(0, 0);
}

function setBottomBar(show) {
  $("bottomBar").classList.toggle("show", show);
}

// 上のタブが画面外にあるときだけ、少し上へ戻すと下部にジャンル切り替えを出す
let lastScrollY = window.scrollY;
let scrolledUp = 0;

function onScroll() {
  const delta = window.scrollY - lastScrollY;
  lastScrollY = window.scrollY;
  scrolledUp = delta > 0 ? 0 : scrolledUp - delta;
  const tabsVisible = $("tabs").getBoundingClientRect().bottom > 0;
  if (view !== "list" || tabsVisible || delta > 0) {
    setBottomBar(false);
  } else if (scrolledUp >= BAR_SHOW_SCROLL_UP_PX) {
    setBottomBar(true);
  }
}

async function toggleRakuchin() {
  if (rakuchin) {
    rakuchin = null;
    notify("楽チン機能を終了しました。");
    render();
    return;
  }
  if (selected.size && !confirm("今の選択を、前回の購入内容に置き換えますか？")) return;
  try {
    const plan = await api("/api/rakuchin", {});
    rakuchin = { previous: plan.previous };
    selected = new Set(plan.kept_ids);
    saveSelection();
    const replaced = plan.previous.length - plan.kept_ids.length;
    notify([
      `楽チン機能：前回の${plan.previous.length}商品のうち${plan.kept_ids.length}商品を選択しました。`,
      replaced ? `値段が変わった・なくなった${replaced}商品の分を選んでください。` : "",
      plan.warning,
    ].join(""));
    render();
  } catch (e) {
    notify(e.message);
  }
}

["tabs", "bottomTabs"].forEach((id) => {
  CATEGORIES.forEach((name) => {
    const tab = document.createElement("button");
    tab.className = "tab";
    tab.textContent = name;
    tab.onclick = () => selectCategory(name);
    $(id).append(tab);
  });
});

document.querySelectorAll(".nav").forEach((b) => { b.onclick = () => showView(b.dataset.view); });
window.addEventListener("scroll", onScroll, { passive: true });
$("search").oninput = render;
$("prevFilter").onchange = render;
$("sort").onchange = (e) => { sort = e.target.value; render(); };
$("selCategory").onclick = () => setSelected(products.filter((p) => p.category === category).map((p) => p.id), true);
$("sel3").onclick = () => setSelected(products.filter((p) => p.category !== DAILY).map((p) => p.id), true);
$("selShown").onclick = () => setSelected(shownProducts().map((p) => p.id), true);
$("selNone").onclick = () => setSelected([...selected], false);
$("refreshBtn").onclick = crawl;
$("retryBtn").onclick = crawl;
$("closeError").onclick = () => { $("loading").hidden = true; };
$("backToList").onclick = () => showView("list");
$("rakuchinBtn").onclick = toggleRakuchin;
$("rakuchinGo").onclick = () => showView("selected");
$("clearSelected").onclick = () => {
  if (confirm(`選択中の${selected.size}商品をすべて解除しますか？`)) setSelected([...selected], false);
};
$("sheetLoad").onclick = async () => {
  await api("/api/settings", { spreadsheet_url: $("sheetUrl").value, sheet_name: $("sheetTab").value });
  loadTabs($("sheetTab").value);
};
function confirmMessage() {
  if (!$("cooperative").checked) {
    return "指定したシートの既存の商品データをすべて削除し、今回選択した商品に置き換えます。よろしいですか？";
  }
  const genres = CATEGORIES.filter((name) => selectedProducts().some((p) => p.category === name));
  return `協力モード：指定したシートの「${genres.join("・")}」の商品データだけを今回選択した商品に置き換えます。`
    + "ほかのジャンルの商品データはそのまま残します。よろしいですか？";
}

$("confirmBtn").onclick = () => {
  $("confirmText").textContent = confirmMessage();
  $("confirmDialog").showModal();
};
$("cooperative").checked = (() => {
  try {
    return localStorage.getItem(COOPERATIVE_KEY) === "1";
  } catch {
    return false;
  }
})();
$("cooperative").onchange = () => {
  try {
    localStorage.setItem(COOPERATIVE_KEY, $("cooperative").checked ? "1" : "");
  } catch {
    // 保存できなくても今回の転記には反映される
  }
};
$("dialogNo").onclick = () => {
  $("confirmDialog").close();
  showView("list");
};
$("dialogYes").onclick = () => {
  $("confirmDialog").close();
  transcribe();
};

render();
loadProducts().then((data) => { if (data.stale) crawl(); }).catch((e) => notify(e.message));
