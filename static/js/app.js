const TOKEN_KEY = "beannote_token";
const LANG_KEY = "beannote_lang";
const i18nManager = {
  SUPPORTED_LANGUAGES: ["da", "en"],
  FALLBACK_LANG: "en",
  supported() {
    const fromConfig = state.config?.supported_languages;
    if (Array.isArray(fromConfig) && fromConfig.length) return fromConfig;
    const langs = state.config?.langs;
    if (langs && typeof langs === "object") return Object.keys(langs);
    return this.SUPPORTED_LANGUAGES;
  },
  labels() {
    return state.config?.langs || { da: "Dansk", en: "English" };
  },
  primaryTag(lang) {
    return String(lang || "").toLowerCase().trim().replaceAll("_", "-").split("-")[0];
  },
  normalize(lang) {
    const raw = String(lang || "").toLowerCase().trim().replaceAll("_", "-");
    const supported = this.supported();
    if (supported.includes(raw)) return raw;
    const code = raw.split("-")[0];
    if (supported.includes(code)) return code;
    const fallback = this.FALLBACK_LANG;
    return supported.includes(fallback) ? fallback : (supported[0] || "en");
  },
  fromDevice() {
    const supported = this.supported();
    const locales = [];
    try {
      if (Array.isArray(navigator.languages)) locales.push(...navigator.languages);
    } catch { /* ignore */ }
    try {
      if (navigator.language) locales.push(navigator.language);
    } catch { /* ignore */ }
    for (const locale of locales) {
      const code = this.primaryTag(locale);
      if (supported.includes(code)) return code;
    }
    return this.FALLBACK_LANG;
  },
  preferred() {
    const stored = localStorage.getItem(LANG_KEY);
    if (stored) return this.normalize(stored);
    return this.normalize(this.fromDevice());
  },
  active() {
    return this.normalize(state.config?.lang || localStorage.getItem(LANG_KEY) || this.fromDevice());
  },
  getLocalized(jsonObj, activeLang, fallbackLang) {
    const fallback = fallbackLang || this.FALLBACK_LANG;
    if (jsonObj == null || jsonObj === "") return "";
    if (Array.isArray(jsonObj)) return jsonObj;
    if (typeof jsonObj === "string") {
      const trimmed = jsonObj.trim();
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try { return this.getLocalized(JSON.parse(trimmed), activeLang, fallback); }
        catch { return jsonObj; }
      }
      return jsonObj;
    }
    if (typeof jsonObj !== "object") return jsonObj;
    const brewKeys = ["recommended_method", "grind_size", "water_temp", "brew_ratio"];
    const hasLangKey = this.supported().some((code) => Object.prototype.hasOwnProperty.call(jsonObj, code));
    if (!hasLangKey && brewKeys.some((key) => key in jsonObj)) return jsonObj;
    const pick = (code) => {
      const value = jsonObj[code];
      if (value == null) return null;
      if (typeof value === "string" && !value.trim()) return null;
      if (Array.isArray(value) && !value.length) return null;
      if (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length) return null;
      return value;
    };
    const lang = this.normalize(activeLang || this.active());
    const fb = this.normalize(fallback);
    return pick(lang)
      ?? pick(fb)
      ?? this.supported().map(pick).find((value) => value != null)
      ?? Object.values(jsonObj).find((value) => value != null && value !== "")
      ?? "";
  },
  setLocalized(jsonObj, value, lang) {
    const code = this.normalize(lang || this.active());
    let map = {};
    if (jsonObj && typeof jsonObj === "object" && !Array.isArray(jsonObj)) {
      map = { ...jsonObj };
    } else if (typeof jsonObj === "string" && jsonObj.trim()) {
      map[this.FALLBACK_LANG] = jsonObj;
    }
    map[code] = value;
    return map;
  },
  t(key, vars = {}) {
    const table = state.config?.strings || {};
    let text = table[key] || key;
    Object.entries(vars).forEach(([k, v]) => { text = text.replace(`{${k}}`, v); });
    return text;
  },
  applyToDom() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = this.t(key);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.setAttribute("placeholder", this.t(key));
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      const key = el.getAttribute("data-i18n-aria");
      if (key) el.setAttribute("aria-label", this.t(key));
    });
    const next = this.active();
    document.querySelectorAll("[data-setlang]").forEach((btn) => {
      const on = btn.dataset.setlang === next;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  },
  setLanguage(lang) {
    const next = this.normalize(lang);
    localStorage.setItem(LANG_KEY, next);
    document.documentElement.lang = next;
    const strings = state.i18n?.[next] || state.config?.strings || {};
    state.config = { ...(state.config || {}), lang: next, strings };
    refreshFlavorCatalog(next);
    this.applyToDom();
    if (worldMap) {
      worldMap.eachLayer((layer) => {
        const popup = layer.getPopup?.();
        const el = popup?.getElement?.();
        el?.querySelectorAll("[data-i18n]").forEach((node) => {
          const key = node.getAttribute("data-i18n");
          if (key) node.textContent = this.t(key);
        });
      });
    }
    render();
  },
  renderLanguageSwitcher() {
    const current = this.active();
    const langs = this.supported();
    const labels = this.labels();
    const buttons = langs.map((code) => {
      const on = current === code;
      const label = code.toUpperCase();
      return `<button type="button" data-setlang="${code}" class="${on ? "is-on" : ""}" aria-pressed="${on ? "true" : "false"}" title="${esc(labels[code] || code)}">${esc(label)}</button>`;
    }).join("");
    return `<div class="lang-switch" role="group" aria-label="${esc(this.t("language"))}">${buttons}</div>`;
  },
};
const state = {
  config: null,
  i18n: { da: {}, en: {} },
  user: null,
  tab: "explore",
  authMode: "login",
  beans: [],
  search: "",
  selectedId: null,
  profile: null,
  scan: null,
  editScan: false,
  editBean: false,
  toast: "",
  busy: false,
  busyLabel: "",
  busyLoader: "",
  busyMessage: "",
  beanFilter: "all",
  beanSort: "newest",
  exploreMode: "cards",
  suitabilityFilter: "",
  savedPrompt: null,
  supportOpen: false,
  journalOpen: false,
  journalOpenBeans: {},
  journal: [],
  recipeTab: "mine",
  rateOpen: false,
  gearQuery: "",
  gearKind: "espresso_machine",
  gearHit: null,
  gearCandidates: [],
  gearPickerOpen: false,
  gearCustomOpen: false,
  gearEditId: null,
  gearCustomName: "",
  gearCustomBrand: "",
  gearCustomImage: "",
  searchTimer: null,
  rate: {
    brew_method: "V60", rating: 4, acidity: 3, sweetness: 3, body: 3, aftertaste: 3,
    notes: "", grind_setting: "", coffee_grams: "", water_grams: "", brew_time: "",
    espresso_machine: "", grinder: "", brewMethodManual: false,
  },
};
let originMap = null;
let worldMap = null;

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");
const t = (key, vars = {}) => i18nManager.t(key, vars);
const COFFEE_LOADERS = ["espresso", "grinder", "pour_over"];
const LOADER_STATUS = {
  espresso: "loader_extract",
  grinder: "loader_grind",
  pour_over: "loader_drip",
};
const getLocalized = (jsonObj, lang, fallback) => i18nManager.getLocalized(jsonObj, lang, fallback);
const normalizeLang = (lang) => i18nManager.normalize(lang);
const activeLang = () => i18nManager.active();
const applyLanguage = (lang) => i18nManager.setLanguage(lang);
const isAdmin = () => !!state.user?.is_admin;

function startBusy() {
  state.busy = true;
  state.busyLoader = COFFEE_LOADERS[Math.floor(Math.random() * 3)];
  state.busyMessage = LOADER_STATUS[state.busyLoader] || "loader_brew";
}

const SCAN_MATCH_CUTOFF = 0.85;

function existingScanMatch(scan) {
  if (!scan) return null;
  const hit = scan.scan_match?.id ? scan.scan_match : scan.similar?.[0];
  if (!hit?.id) return null;
  if (scan.scan_action === "rate") return hit;
  if (Number(hit.confidence || 0) >= SCAN_MATCH_CUTOFF) return hit;
  return null;
}

function stopBusy() {
  state.busy = false;
  state.busyLabel = "";
  state.busyLoader = "";
  state.busyMessage = "";
}

function coffeeLoaderSvg(kind) {
  if (kind === "grinder") {
    return `<svg class="bn-coffee-svg" viewBox="0 0 160 160" aria-hidden="true">
      <ellipse class="bn-pulse" cx="80" cy="148" rx="30" ry="5.5" fill="#b85c38" opacity="0.22"/>
      <path d="M54 16h52l-9 30H63z" fill="#faf6f0" stroke="#3c2a21" stroke-width="2.4" stroke-linejoin="round"/>
      <ellipse class="bn-bounce" cx="70" cy="32" rx="5.2" ry="3.8" fill="#3c2a21"/>
      <path d="M67.6 32h5" stroke="#8c7a6b" stroke-width="1.1" stroke-linecap="round"/>
      <ellipse class="bn-bounce bn-delay-1" cx="88" cy="29" rx="4.6" ry="3.4" fill="#b85c38"/>
      <ellipse class="bn-bounce bn-delay-2" cx="78" cy="38" rx="4.3" ry="3.2" fill="#5c3d30"/>
      <rect x="50" y="46" width="60" height="54" rx="13" fill="#3c2a21"/>
      <rect x="56" y="52" width="48" height="42" rx="10" fill="#4a3328"/>
      <g class="bn-spin">
        <circle cx="80" cy="73" r="16" fill="#6b4a38" stroke="#e8d8c8" stroke-width="2"/>
        <line x1="80" y1="59" x2="80" y2="87" stroke="#faf6f0" stroke-width="2" stroke-linecap="round"/>
        <line x1="66" y1="73" x2="94" y2="73" stroke="#faf6f0" stroke-width="2" stroke-linecap="round"/>
        <line x1="70" y1="63" x2="90" y2="83" stroke="#faf6f0" stroke-width="1.6" stroke-linecap="round"/>
        <line x1="90" y1="63" x2="70" y2="83" stroke="#faf6f0" stroke-width="1.6" stroke-linecap="round"/>
      </g>
      <circle class="bn-spin-rev" cx="80" cy="73" r="5.4" fill="#3c2a21" stroke="#b85c38" stroke-width="1.8"/>
      <path d="M72 100h16v8H72z" fill="#5c4033"/>
      <ellipse class="bn-drip" cx="76" cy="118" rx="3.1" ry="2.2" fill="#3c2a21"/>
      <ellipse class="bn-drip bn-delay-1" cx="86" cy="124" rx="2.7" ry="2" fill="#b85c38"/>
      <ellipse class="bn-drip bn-delay-2" cx="80" cy="132" rx="3.3" ry="2.3" fill="#4a3328"/>
    </svg>`;
  }
  if (kind === "pour_over") {
    return `<svg class="bn-coffee-svg" viewBox="0 0 160 160" aria-hidden="true">
      <defs>
        <linearGradient id="bnPourCoffee" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#b85c38"/>
          <stop offset="100%" stop-color="#3c2a21"/>
        </linearGradient>
      </defs>
      <ellipse class="bn-pulse" cx="80" cy="150" rx="32" ry="5" fill="#b85c38" opacity="0.2"/>
      <path class="bn-pour" d="M116 12c-4 16-22 26-32 42" fill="none" stroke="#6d8f88" stroke-width="3.6" stroke-linecap="round"/>
      <path d="M128 6c6 2 12 8 12 16" fill="none" stroke="#3c2a21" stroke-width="2.2" stroke-linecap="round"/>
      <path d="M42 40h76L94 90H66z" fill="#faf6f0" stroke="#b85c38" stroke-width="2.6" stroke-linejoin="round"/>
      <path d="M54 54h52M60 68h40M68 82h24" stroke="#e8d8c8" stroke-width="1.5" stroke-linecap="round"/>
      <ellipse class="bn-pulse" cx="80" cy="56" rx="16" ry="4.4" fill="#b85c38" opacity="0.38"/>
      <rect x="76" y="90" width="8" height="5" rx="1.5" fill="#b85c38"/>
      <path class="bn-drip" d="M80 98c0 0-3.1 4.2 0 7 3.1-2.8 0-7 0-7z" fill="#b85c38"/>
      <path class="bn-drip bn-delay-1" d="M80 106c0 0-2.6 3.6 0 6 2.6-2.4 0-6 0-6z" fill="#3c2a21"/>
      <path class="bn-drip bn-delay-2" d="M80 113c0 0-2.3 3.2 0 5.4 2.3-2.2 0-5.4 0-5.4z" fill="#6b4a38"/>
      <path d="M52 118h56l7 28H45z" fill="#fff" stroke="#3c2a21" stroke-width="2.2" stroke-linejoin="round"/>
      <path class="bn-fill-shot" d="M55 128h50l3.5 14H51.5z" fill="url(#bnPourCoffee)"/>
      <path d="M108 124c10 2 10 18 0 20" fill="none" stroke="#3c2a21" stroke-width="2.2"/>
    </svg>`;
  }
  return `<svg class="bn-coffee-svg" viewBox="0 0 160 160" aria-hidden="true">
    <defs>
      <linearGradient id="bnEspressoShot" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#e8c9a0"/>
        <stop offset="38%" stop-color="#b85c38"/>
        <stop offset="100%" stop-color="#3c2a21"/>
      </linearGradient>
    </defs>
    <ellipse class="bn-pulse" cx="84" cy="150" rx="34" ry="5.5" fill="#b85c38" opacity="0.2"/>
    <path class="bn-pulse" d="M70 28c0-10 8-16 14-16" fill="none" stroke="#c4b09a" stroke-width="2.3" stroke-linecap="round"/>
    <path class="bn-pulse bn-delay-1" d="M86 28c2-10 10-14 16-12" fill="none" stroke="#c4b09a" stroke-width="2.3" stroke-linecap="round"/>
    <rect x="8" y="48" width="46" height="14" rx="7" fill="#3c2a21"/>
    <circle cx="16" cy="55" r="5" fill="#b85c38"/>
    <rect x="48" y="42" width="18" height="26" rx="4" fill="#5a3d30"/>
    <path d="M64 40h40a8 8 0 0 1 8 8v8c0 12-9 20-20 20H76c-11 0-20-8-20-20v-8a8 8 0 0 1 8-8z" fill="#3c2a21"/>
    <ellipse cx="84" cy="48" rx="14" ry="5" fill="#6b4a38"/>
    <rect x="74" y="76" width="5" height="10" rx="1.5" fill="#3c2a21"/>
    <rect x="91" y="76" width="5" height="10" rx="1.5" fill="#3c2a21"/>
    <line class="bn-stream" x1="76.5" y1="86" x2="76.5" y2="106" stroke="#b85c38" stroke-width="2.6" stroke-linecap="round"/>
    <line class="bn-stream bn-delay-1" x1="93.5" y1="86" x2="93.5" y2="106" stroke="#6b3a28" stroke-width="2.6" stroke-linecap="round"/>
    <path class="bn-drip" d="M76.5 104c0 0-3 4.2 0 7 3-2.8 0-7 0-7z" fill="#b85c38"/>
    <path class="bn-drip bn-delay-2" d="M93.5 107c0 0-3 4.2 0 7 3-2.8 0-7 0-7z" fill="#3c2a21"/>
    <path d="M54 112h56l-6 28H60z" fill="#faf6f0" stroke="#3c2a21" stroke-width="2.3" stroke-linejoin="round"/>
    <path class="bn-fill-shot" d="M58 120h48l-3.2 16H61.2z" fill="url(#bnEspressoShot)"/>
    <path class="bn-pulse" d="M59 118h46l-1 4H60z" fill="#e8c9a0"/>
    <path d="M110 116c12 3 12 20 0 24" fill="none" stroke="#3c2a21" stroke-width="2.3"/>
    <path d="M112 128h10" stroke="#3c2a21" stroke-width="2.3" stroke-linecap="round"/>
  </svg>`;
}

function coffeeLoaderOverlay() {
  if (!state.busy) return "";
  const kind = COFFEE_LOADERS.includes(state.busyLoader) ? state.busyLoader : COFFEE_LOADERS[0];
  const msg = t(state.busyMessage || state.busyLabel || "loader_brew");
  return `<div class="bn-loader-overlay" role="status" aria-live="polite" aria-busy="true">
    <div class="bn-loader-card">
      <div class="bn-loader-stage" data-loader="${esc(kind)}">${coffeeLoaderSvg(kind)}</div>
      <p class="bn-loader-msg">${esc(msg)}</p>
    </div>
  </div>`;
}

function parseSuitableFor(value) {
  try {
    const parsed = Array.isArray(value) ? value : JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed.map((item) => String(item || "").trim()).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function localizeSuitable(tag) {
  const raw = String(tag || "").trim();
  if (!raw) return raw;
  const table = state.config?.suitable_i18n || {};
  const hit = table[raw] || table[raw.toLowerCase()];
  return (hit && (hit[activeLang()] || hit.da)) || raw;
}

function matchesSuitable(tags, filter) {
  if (!filter) return true;
  const aliases = {
    espresso: ["espresso", "machines", "maskine"],
    filter: ["filter", "pour-over", "pour over", "v60", "drip"],
    milk: ["mælkedrikke", "milk", "latte", "macchiato"],
    superautomatic: ["fuldautomat", "superautomatic", "super-automatic", "super automatic", "bean to cup", "bean-to-cup"],
    press: ["stempelkande", "french press", "plunger"],
  };
  const needles = aliases[filter] || [filter];
  return parseSuitableFor(tags).some((tag) => {
    const low = String(tag || "").toLowerCase();
    return needles.some((needle) => low.includes(needle));
  });
}

function availableSuitableFilters() {
  const catalog = [
    ["espresso", "filter_suitable_espresso"],
    ["filter", "filter_suitable_filter"],
    ["milk", "filter_suitable_milk"],
    ["superautomatic", "filter_suitable_superautomatic"],
    ["press", "filter_suitable_press"],
  ];
  const beans = state.beans || [];
  return catalog.filter(([id]) => beans.some((bean) => matchesSuitable(bean.suitable_for, id)));
}

function visibleBeans() {
  const filtered = (state.beans || []).filter((bean) => matchesSuitable(bean.suitable_for, state.suitabilityFilter));
  return sortVisibleBeans(filtered, state.beanSort || "newest");
}

const ORIGIN_PLACEHOLDERS = new Set([
  "", "oprindelse", "origin", "unknown", "ukendt", "n/a", "na", "none",
  "herkunft", "origine", "origen", "-", "–", "—",
]);

function isValidOrigin(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return false;
  const low = raw.toLowerCase();
  if (ORIGIN_PLACEHOLDERS.has(low)) return false;
  const label = String(t("origin") || "").trim();
  if (label && (raw === label || low === label.toLowerCase())) return false;
  return true;
}

function beanOriginText(bean) {
  for (const value of [bean?.origin, bean?.country]) {
    if (isValidOrigin(value)) return String(value).trim();
  }
  return "";
}

function beanCardMeta(bean) {
  const roaster = String(bean?.roaster || "").trim();
  const origin = beanOriginText(bean);
  if (roaster && origin) return `${esc(roaster)} · ${esc(origin)}`;
  return esc(roaster);
}

function roastBadgeKind(bean) {
  const raw = String(bean?.roast_level || "").trim();
  const blob = raw.toLowerCase();
  if (blob) {
    if (/(medium[-\s]?(dark|mørk|mork)|mellem[-\s]?(mørk|mork)|mellemmørk|mellemmork|mellemrist)/.test(blob)) return "medium";
    if (/(medium[-\s]?(light|lys)|mellem[-\s]?lys|mellemlys)/.test(blob)) return "medium";
    if (/(?:^|[^a-zæøå])(?:mørk|mork|dark)(?:[^a-zæøå]|$)/.test(blob) || /italian|french\s*roast/.test(blob)) return "dark";
    if (/(?:^|[^a-zæøå])(?:lys|light|blond)(?:[^a-zæøå]|$)/.test(blob)) return "light";
    if (/(medium|mellem|city)/.test(blob)) return "medium";
  }
  const score = Number(bean?.roast_level_score ?? bean?.roaster_roast_level);
  if (Number.isFinite(score) && score >= 1 && score <= 5) {
    if (score <= 2) return "light";
    if (score >= 4) return "dark";
    return "medium";
  }
  return "";
}

function roastBadgeLabel(bean, kind) {
  const raw = String(bean?.roast_level || "").trim();
  const da = activeLang() === "da";
  const catalog = {
    light: da ? "Lys Ristning" : "Light Roast",
    medium: da ? "Mellem Ristning" : "Medium Roast",
    dark: da ? "Mørk Ristning" : "Dark Roast",
  };
  if (!raw) return catalog[kind] || "";
  if (/ristning|roast/i.test(raw)) return raw;
  if (/^(lys|light)$/i.test(raw)) return catalog.light;
  if (/^(mørk|mork|dark)$/i.test(raw)) return catalog.dark;
  if (/^(medium|mellem)$/i.test(raw)) return catalog.medium;
  const suffix = da ? "Ristning" : "Roast";
  if (raw.length <= 18) return `${raw} ${suffix}`;
  return raw;
}

function roastBadge(bean) {
  const kind = roastBadgeKind(bean);
  if (!kind) return "";
  return `<span class="roast-badge roast-badge-${kind}">${esc(roastBadgeLabel(bean, kind))}</span>`;
}

function beanRatingValue(bean) {
  const n = Number(bean?.avg_rating);
  return Number.isFinite(n) ? n : 0;
}

function beanRatingCount(bean) {
  const n = Number(bean?.rating_count ?? bean?.ratings_count ?? bean?.review_count);
  return Number.isFinite(n) ? n : 0;
}

function compareNewest(a, b) {
  const byDate = String(b?.created_at || "").localeCompare(String(a?.created_at || ""));
  if (byDate) return byDate;
  return (Number(b?.id) || 0) - (Number(a?.id) || 0);
}

function compareBeanText(left, right) {
  return String(left || "").localeCompare(String(right || ""), activeLang() || "en", {
    sensitivity: "base",
    numeric: true,
  });
}

function sortVisibleBeans(beans, mode) {
  const rows = beans.slice();
  if (mode === "rating_desc") {
    rows.sort((a, b) => (beanRatingValue(b) - beanRatingValue(a)) || (beanRatingCount(b) - beanRatingCount(a)) || compareNewest(a, b));
  } else if (mode === "ratings_count_desc") {
    rows.sort((a, b) => (beanRatingCount(b) - beanRatingCount(a)) || (beanRatingValue(b) - beanRatingValue(a)) || compareNewest(a, b));
  } else if (mode === "roaster_asc") {
    rows.sort((a, b) => compareBeanText(a.roaster, b.roaster) || compareBeanText(a.name, b.name) || compareNewest(a, b));
  } else if (mode === "name_asc") {
    rows.sort((a, b) => compareBeanText(a.name, b.name) || compareBeanText(a.roaster, b.roaster) || compareNewest(a, b));
  } else {
    rows.sort(compareNewest);
  }
  return rows;
}

function localizeTag(tag) {
  const raw = String(tag || "").trim();
  if (!raw) return raw;
  const table = state.config?.flavor_i18n || {};
  const hit = table[raw] || table[raw.toLowerCase()] || table[raw.replace(/\s+/g, " ")];
  return (hit && (hit[activeLang()] || hit.da)) || raw;
}

function refreshFlavorCatalog(lang) {
  const table = state.config?.flavor_i18n || {};
  const next = normalizeLang(lang);
  const seen = new Set();
  const notes = [];
  Object.values(table).forEach((entry) => {
    const label = entry?.[next] || entry?.da;
    if (label && !seen.has(label)) {
      seen.add(label);
      notes.push(label);
    }
  });
  if (notes.length) state.config.flavor_notes = notes;
}

const token = () => localStorage.getItem(TOKEN_KEY) || "";
const GEAR_IMG_FALLBACK = "/static/img/gear/placeholder.svg";
const mediaSrc = (url) => {
  if (!url) return "";
  if (url.startsWith("data:") || url.startsWith("http") || url.startsWith("/static/")) return url;
  const name = url.split("/").pop();
  return `/media/${encodeURIComponent(name)}`;
};

function photoImg(url, fallback, className, extraAttrs = "") {
  const src = mediaSrc(url);
  const fb = mediaSrc(fallback);
  if (!src && !fb) return "";
  const primary = src || fb;
  const extra = src && fb && src !== fb
    ? ` data-fallback="${esc(fb)}" onerror="if(this.dataset.fallback){this.src=this.dataset.fallback;this.removeAttribute('data-fallback');}"`
    : "";
  return `<img src="${esc(primary)}" alt="" class="${className}"${extra}${extraAttrs}>`;
}

function errorDetail(data) {
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail[0]) {
    const first = detail[0];
    if (typeof first === "string" && first.trim()) return first;
    if (typeof first?.msg === "string" && first.msg.trim()) return first.msg;
  }
  if (typeof data?.message === "string" && data.message.trim()) return data.message;
  return "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const isForm = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (isForm) {
    delete headers["Content-Type"];
    delete headers["content-type"];
  } else if (!headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const res = await fetch(path, { credentials: "include", ...options, headers });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = errorDetail(data) || "error";
    const err = new Error(detail);
    err.detail = detail;
    throw err;
  }
  return data;
}

async function waitForJob(job, { interval = 700, timeoutMs = 270000 } = {}) {
  if (job && !job.job_id && (job.scan_action || job.name || job.status === "enriched" || job.bean)) {
    return job;
  }
  if (!job?.job_id) {
    const err = new Error("ocr_fail");
    err.detail = "ocr_fail";
    throw err;
  }
  let current = job;
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (current.status === "done") return current.result || {};
    if (current.status === "failed") {
      const detail = current.error || "ocr_fail";
      const err = new Error(detail);
      err.detail = detail;
      throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
    current = await api(`/api/jobs/${current.job_id}`);
  }
  const err = new Error("scan_timeout");
  err.detail = "scan_timeout";
  throw err;
}

function toast(message) {
  state.toast = message;
  render();
  setTimeout(() => { if (state.toast === message) { state.toast = ""; render(); } }, 2800);
}

function setAuth(payload) {
  if (payload?.token) localStorage.setItem(TOKEN_KEY, payload.token);
  state.user = payload?.user || null;
}

async function boot() {
  const lang = i18nManager.preferred();
  try {
    state.config = await api(`/api/config?lang=${lang}`);
    state.i18n = state.config.i18n || {};
    const packMissing = i18nManager.supported().some((code) => !state.i18n[code]);
    if (packMissing) {
      try { state.i18n = await api("/api/i18n"); } catch { /* keep config strings */ }
    }
    state.config.lang = lang;
    state.config.strings = state.i18n[lang] || state.config.strings || {};
    state.user = state.config.user;
    if (state.user) {
      await loadBeans();
      await loadJournal();
    }
  } catch {
    state.config = { lang, strings: {}, langs: { da: "Dansk", en: "English" }, supported_languages: i18nManager.SUPPORTED_LANGUAGES, fallback_lang: i18nManager.FALLBACK_LANG, providers: {}, brew_methods: [], flavor_notes: [] };
  }
  document.documentElement.lang = lang;
  const params = new URLSearchParams(location.search);
  if (params.get("auth_error")) toast(t("oauth_unavailable"));
  render();
  bindScanInput();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).then((reg) => reg.update()).catch(() => {});
  }
}

async function loadBeans() {
  const q = new URLSearchParams({ search: state.search });
  if (state.beanFilter === "favorites") q.set("favorites", "1");
  state.beans = await api(`/api/beans?${q}`);
  if (state.suitabilityFilter && !availableSuitableFilters().some(([id]) => id === state.suitabilityFilter)) {
    state.suitabilityFilter = "";
  }
}

async function loadJournal() {
  try {
    const data = await api("/api/journal");
    state.journal = data?.entries || [];
  } catch {
    state.journal = [];
  }
}

async function persistGear(next) {
  const result = await api("/api/gear", { method: "PUT", body: JSON.stringify(next) });
  if (result.user) state.user = result.user;
  toast(t("gear_saved"));
  render();
}

async function addGearItem(raw) {
  const hit = normalizeClientGear(raw);
  if (!hit.name) return;
  const gear = userGear();
  const specs = gear.gear_specs.filter((item) => item.id !== hit.id);
  specs.push(hit);
  const kind = gearKindOf(hit);
  state.gearHit = null;
  state.gearQuery = "";
  state.gearCandidates = [];
  await persistGear({
    espresso_machine: kind === "espresso_machine" ? hit.name : gear.espresso_machine,
    grinder: kind === "grinder" ? hit.name : gear.grinder,
    brewer_types: gear.brewer_types,
    gear_specs: specs,
  });
}

function resetScanPreview() {
  state.scan = null;
  state.editScan = false;
  stopBusy();
  state.tab = "scan";
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function openBean(id, tab = "explore") {
  state.selectedId = id;
  state.profile = await api(`/api/beans/${id}`);
  const user = state.profile.user;
  const gear = userGear();
  state.rate = {
    brew_method: user?.brew_method || "V60",
    rating: user?.rating || 4,
    acidity: user?.acidity || 3,
    sweetness: user?.sweetness || 3,
    body: user?.body || 3,
    aftertaste: user?.aftertaste || 3,
    notes: user?.notes || "",
    grind_setting: user?.grind_setting || "",
    coffee_grams: user?.coffee_grams ?? "",
    water_grams: user?.water_grams ?? "",
    brew_time: normalizeBrewTime(user?.brew_time || ""),
    espresso_machine: user?.espresso_machine || gear.espresso_machine || "",
    grinder: user?.grinder || gear.grinder || "",
    brewMethodManual: false,
  };
  applyRecipeDefaults(user);
  state.rateOpen = tab === "rate";
  state.tab = tab === "rate" || tab === "explore" ? "explore" : tab;
  if (state.tab === "rate") state.tab = "explore";
  state.editBean = false;
  state.recipeTab = "mine";
  render();
}

function deviceLocale() {
  try {
    return Intl.NumberFormat().resolvedOptions().locale
      || navigator.language
      || navigator.languages?.[0]
      || document.documentElement.lang
      || "en-US";
  } catch {
    return navigator.language || "en-US";
  }
}

function decimalSeparator() {
  try {
    const part = new Intl.NumberFormat(deviceLocale()).formatToParts(1.1)
      .find((item) => item.type === "decimal");
    return part?.value === "," ? "," : ".";
  } catch {
    return ".";
  }
}

function parseGrams(value) {
  if (value === "" || value == null) return null;
  let cleaned = String(value).trim().replace(/[\s\u00a0]/g, "").replace(/[^\d,.\-]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === "," || cleaned === ".") return null;
  const comma = cleaned.includes(",");
  const dot = cleaned.includes(".");
  if (comma && dot) {
    if (cleaned.lastIndexOf(",") > cleaned.lastIndexOf(".")) {
      cleaned = cleaned.replace(/\./g, "").replace(",", ".");
    } else {
      cleaned = cleaned.replace(/,/g, "");
    }
  } else if (comma) {
    cleaned = cleaned.split(",").length > 2
      ? cleaned.replace(/,/g, "")
      : cleaned.replace(",", ".");
  }
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

function formatGramsInput(value) {
  const n = parseGrams(value);
  if (n == null) return "";
  const rounded = Math.round(n * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return text.replace(".", decimalSeparator());
}

function formatGramsLabel(value) {
  return formatGramsInput(value);
}

function sanitizeGramsTyping(raw) {
  let out = "";
  let sep = "";
  for (const ch of String(raw ?? "")) {
    if (ch >= "0" && ch <= "9") {
      if (sep && out.slice(out.indexOf(sep) + 1).length >= 2) continue;
      out += ch;
    } else if ((ch === "," || ch === ".") && !sep) {
      sep = ch;
      out += ch;
    }
  }
  return out;
}

function formatBrewClock(totalSec) {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function brewTimeSeconds(raw) {
  const text = String(raw || "").trim();
  if (!text) return null;
  const labeled = text.match(/^(\d+(?:[.,]\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|min|m)$/i);
  if (labeled) {
    const n = Number(String(labeled[1]).replace(",", "."));
    if (!Number.isFinite(n)) return null;
    return labeled[2].toLowerCase().startsWith("m") ? Math.round(n * 60) : Math.round(n);
  }
  const clock = text.match(/^(\d+)\s*[:]\s*(\d{1,2})(?:\s*(mins?|minutes?|m|secs?|seconds?|s))?$/i);
  if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
  const dotted = text.match(/^(\d+)\s*[.]\s*(\d{2})(?:\s*(mins?|minutes?|m|secs?|seconds?|s))?$/i);
  if (dotted) return Number(dotted[1]) * 60 + Number(dotted[2]);
  return null;
}

function formatBrewDuration(totalSec) {
  const sec = Math.max(0, Math.round(Number(totalSec) || 0));
  if (sec < 60) return `${sec}s`;
  return formatBrewClock(sec);
}

function normalizeBrewTime(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const sec = brewTimeSeconds(text);
  return sec == null ? text : formatBrewDuration(sec);
}

function brewTimeInput() {
  return `<input id="brew_time" value="${esc(state.rate.brew_time || "")}" class="time-input mt-1 min-h-11 w-full rounded-xl border border-latte bg-white px-3 text-sm" data-i18n-placeholder="brew_time_ph" placeholder="${esc(t("brew_time_ph"))}" autocomplete="off" spellcheck="false">`;
}

function applyRecipeDefaults(existing) {
  const machines = gearItemsOfKind("espresso_machine");
  const grinders = gearItemsOfKind("grinder");
  if (machines.length === 1) state.rate.espresso_machine = machines[0];
  if (grinders.length === 1) state.rate.grinder = grinders[0];
  const implied = impliedBrewMethod();
  const saved = String(existing?.brew_method || "").trim();
  if (saved && implied && saved !== implied) {
    state.rate.brewMethodManual = true;
    state.rate.brew_method = saved;
    return;
  }
  if (implied) {
    state.rate.brewMethodManual = false;
    state.rate.brew_method = implied;
    return;
  }
  state.rate.brewMethodManual = true;
  state.rate.brew_method = saved || state.rate.brew_method || "V60";
}

function clampHalf(value, min = 0.5, max = 5) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, Math.round(n * 2) / 2));
}

function clampDot(value) {
  const n = Math.round(Number(value) || 0);
  return Math.max(1, Math.min(5, n));
}

function starFill(score, n) {
  if (score >= n) return "full";
  if (score >= n - 0.5) return "half";
  return "empty";
}

function bagFallback(heightClass = "h-[180px]") {
  return `<div class="coffee-pattern flex ${heightClass} w-full items-center justify-center">
    <svg viewBox="0 0 80 64" width="64" height="52" fill="none" aria-hidden="true">
      <rect x="22" y="10" width="36" height="46" rx="5" fill="#faf6f0"/>
      <path d="M22 22h36" stroke="#b85c38" stroke-width="3"/>
      <rect x="32" y="10" width="16" height="6" rx="2" fill="#e8d8c8"/>
      <circle cx="40" cy="40" r="9" stroke="#3c2a21" stroke-width="2"/>
    </svg>
  </div>`;
}

function bagThumb() {
  return `<div class="coffee-pattern flex h-16 w-16 shrink-0 items-center justify-center rounded">
    <svg viewBox="0 0 80 64" width="28" height="24" fill="none" aria-hidden="true">
      <rect x="22" y="10" width="36" height="46" rx="5" fill="#faf6f0"/>
      <path d="M22 22h36" stroke="#b85c38" stroke-width="3"/>
      <rect x="32" y="10" width="16" height="6" rx="2" fill="#e8d8c8"/>
      <circle cx="40" cy="40" r="9" stroke="#3c2a21" stroke-width="2"/>
    </svg>
  </div>`;
}

function coffeeMiniCard(bean) {
  const img = photoImg(bean.image_url, bean.snapshot_url, "h-16 w-16 shrink-0 rounded object-cover") || bagThumb();
  return `<div class="flex min-w-[13.5rem] max-w-[16rem] items-center gap-3">
    ${img}
    <div class="min-w-0 flex-1">
      <p class="font-display truncate text-sm font-semibold leading-tight">${esc(bean.name)}</p>
      <p class="truncate text-xs text-muted">${esc(bean.roaster || "")}</p>
      <button type="button" data-open-bean="${bean.id}" class="mt-1.5 min-h-8 rounded-lg border border-latte bg-transparent px-2.5 text-xs font-semibold text-espresso" data-i18n="view_details">${t("view_details")}</button>
    </div>
  </div>`;
}

function flavorList(tags) {
  const localized = getLocalized(tags, activeLang());
  return Array.isArray(localized) ? localized : [];
}

function pills(tags) {
  return flavorList(tags).map((tag) =>
    `<span class="rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-medium text-espresso">${esc(localizeTag(tag))}</span>`
  ).join("");
}

function feedPills(tags) {
  const clean = flavorList(tags)
    .map((tag) => localizeTag(tag))
    .filter((tag) => tag && !/[.!?;:]/.test(tag) && tag.split(/\s+/).length <= 4)
    .slice(0, 3);
  return clean.map((tag) =>
    `<span class="rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-medium text-espresso">${esc(tag)}</span>`
  ).join("");
}

function brewSource(source) {
  const rec = getLocalized(source?.brew_recommendation, activeLang()) || {};
  const flat = rec && typeof rec === "object" && !Array.isArray(rec) ? rec : {};
  return {
    method: flat.recommended_method || source?.recommended_method || "",
    grind: flat.grind_size || source?.grind_size || "",
    temp: flat.water_temp || source?.water_temp || "",
    ratio: flat.brew_ratio || source?.brew_ratio || "",
    usage: flat.usage || flat.mouthfeel || "",
  };
}

function heartBtn(bean, extra = "") {
  const on = !!bean?.is_favorite;
  return `<button type="button" data-fav="${bean.id}" aria-label="${esc(on ? t("favorite_remove") : t("favorite_add"))}"
    class="grid h-10 w-10 place-items-center rounded-full bg-cream/95 text-lg shadow ${extra}">${on ? "❤️" : "♡"}</button>`;
}

function metaBadges(source) {
  const bits = [];
  if (source?.altitude) {
    const raw = String(source.altitude);
    const label = /masl|m\.o\.h|moh/i.test(raw) ? raw : `${raw} ${t("masl")}`;
    bits.push(`<span class="rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-medium">${esc(label)}</span>`);
  }
  if (source?.varietal) {
    bits.push(`<span class="rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-medium">${esc(source.varietal)}</span>`);
  }
  if (source?.roast_date) {
    bits.push(`<span class="rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-medium">${esc(t("roast_date"))}: ${esc(source.roast_date)}</span>`);
  }
  return bits.length ? `<div class="flex flex-wrap gap-1">${bits.join("")}</div>` : "";
}

function originMapBox(source) {
  if (source?.latitude == null || source?.longitude == null) {
    return `<p class="my-3 text-sm text-muted">${t("no_map_coords")}</p>`;
  }
  const label = source.region_full || source.origin || source.name || t("map_origin");
  return `<div id="origin-map" class="h-44 w-full rounded-xl overflow-hidden my-3" data-lat="${source.latitude}" data-lng="${source.longitude}" data-label="${esc(label)}"></div>`;
}

function exploreSegBar() {
  const btn = (attr, id, key, on) =>
    `<button type="button" ${attr}="${id}" data-i18n="${key}" class="${on ? "is-on" : ""}">${esc(t(key))}</button>`;
  return `<div class="explore-seg" role="toolbar" aria-label="${esc(t("filter_all_beans"))}">
    ${btn("data-filter", "all", "filter_all_beans", state.beanFilter === "all")}
    ${btn("data-filter", "favorites", "filter_favorites", state.beanFilter === "favorites")}
    <span class="explore-seg-split" aria-hidden="true"></span>
    ${btn("data-view", "cards", "view_cards", state.exploreMode === "cards")}
    ${btn("data-view", "map", "view_map", state.exploreMode === "map")}
  </div>`;
}

function brewBadge(source) {
  const rec = brewSource(source);
  const compact = [rec.method, rec.grind, rec.temp].filter(Boolean);
  if (!compact.length) return "";
  return `<div class="rounded-xl bg-[#f4ebd9] px-3 py-2">
    <p class="text-sm font-semibold"><span data-i18n="brew_recs">${t("brew_recs")}</span>: ${esc(compact.join(" | "))}</p>
    ${rec.ratio ? `<p class="mt-0.5 text-xs text-muted">${esc(rec.ratio)}</p>` : ""}
  </div>`;
}

function storyBlock(story) {
  const text = getLocalized(story, activeLang());
  if (!text) return "";
  return `<section class="rounded-xl bg-foam px-3 py-3">
    <h3 class="text-sm font-semibold" data-i18n="story_expand">${t("story_expand")}</h3>
    <p class="mt-2 text-sm leading-6 text-espresso/80">${esc(text)}</p>
  </section>`;
}

function journalBits(source) {
  const brew = brewBadge(source);
  const story = storyBlock(source?.story);
  if (!brew && !story) return "";
  return `<div class="grid gap-2 ${brew && story ? "sm:grid-cols-2" : ""}">${brew}${story}</div>`;
}

function progressRow(labelKey, score) {
  const n = Number(score);
  const value = Number.isFinite(n) ? Math.max(0, Math.min(5, n)) : 0;
  const rounded = Math.round(value);
  const display = Number.isInteger(value) || Math.abs(value - rounded) < 0.05
    ? String(rounded)
    : value.toFixed(1);
  const dots = [1, 2, 3, 4, 5].map((i) =>
    `<span class="flavor-dot${i <= rounded ? " on" : ""}" aria-hidden="true"></span>`
  ).join("");
  return `<div class="flex items-center justify-between gap-3">
    <p class="min-w-[6.5rem] shrink-0 text-sm font-semibold" data-i18n="${labelKey}">${esc(t(labelKey))}</p>
    <div class="flex items-center gap-2" role="meter" aria-valuemin="0" aria-valuemax="5" aria-valuenow="${value}" aria-label="${esc(t(labelKey))}">
      <div class="flex items-center gap-1">${dots}</div>
      <p class="text-xs font-semibold tabular-nums text-terracotta">${display}/5</p>
    </div>
  </div>`;
}

function sensoryStrip(scores) {
  if (!scores) return "";
  const rows = [
    ["sense_acidity", scores.acidity],
    ["sense_sweetness", scores.sweetness],
    ["sense_body", scores.body],
    ["sense_aftertaste", scores.aftertaste],
  ].filter(([, score]) => Number(score) > 0);
  if (!rows.length) return "";
  return `<div class="space-y-1.5">${rows.map(([key, score]) => progressRow(key, score)).join("")}</div>`;
}

function starControl(value) {
  const score = clampHalf(value);
  const stars = [1, 2, 3, 4, 5].map((n) =>
    `<button type="button" data-star="${n}" class="star-btn star-${starFill(score, n)}" aria-label="${n}">★</button>`
  ).join("");
  return `<div class="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-latte">
    <div class="flex items-center justify-between gap-3">
      <p class="text-sm font-semibold" data-i18n="rating">${esc(t("rating"))}</p>
      <p id="rating-score" class="text-lg font-bold tabular-nums text-terracotta">${esc(t("rating_score", { score: score.toFixed(1) }))}</p>
    </div>
    <div id="star-row" class="mt-1 flex justify-center gap-0.5" role="slider" aria-valuemin="0.5" aria-valuemax="5" aria-valuenow="${score}">${stars}</div>
  </div>`;
}

function sensoryDotRow(id, key, value) {
  const score = clampDot(value);
  const dots = [1, 2, 3, 4, 5].map((n) =>
    `<button type="button" data-sense="${id}" data-value="${n}" class="sense-dot${n <= score ? " on" : ""}" aria-label="${n}"></button>`
  ).join("");
  return `<div class="flex items-center justify-between gap-3">
    <p class="min-w-[6.5rem] shrink-0 text-sm font-semibold" data-i18n="${key}">${esc(t(key))}</p>
    <div class="flex items-center gap-2">${dots}</div>
  </div>`;
}

function sensorySelectors() {
  return `<div class="space-y-3 rounded-2xl bg-white px-4 py-3 shadow-sm ring-1 ring-latte">
    ${sensoryDotRow("acidity", "sense_acidity", state.rate.acidity)}
    ${sensoryDotRow("sweetness", "sense_sweetness", state.rate.sweetness)}
    ${sensoryDotRow("body", "sense_body", state.rate.body)}
    ${sensoryDotRow("aftertaste", "sense_aftertaste", state.rate.aftertaste)}
  </div>`;
}

function liveSensoryCard() {
  const r = state.rate;
  return `<section id="sensory-preview" class="space-y-2 rounded-2xl bg-white px-3 py-3 shadow-sm ring-1 ring-latte">
    <div class="flex items-center justify-between gap-2">
      <p class="text-sm font-semibold" data-i18n="sensory_preview">${esc(t("sensory_preview"))}</p>
      <p class="text-sm font-bold text-amber-600">★ ${clampHalf(r.rating).toFixed(1)}</p>
    </div>
    ${sensoryStrip(r)}
  </section>`;
}

function syncRateUi() {
  const score = clampHalf(state.rate.rating);
  const scoreEl = $("#rating-score");
  if (scoreEl) scoreEl.textContent = t("rating_score", { score: score.toFixed(1) });
  const row = $("#star-row");
  if (row) row.setAttribute("aria-valuenow", String(score));
  document.querySelectorAll("[data-star]").forEach((btn) => {
    const n = Number(btn.dataset.star);
    btn.classList.remove("star-full", "star-half", "star-empty");
    btn.classList.add(`star-${starFill(score, n)}`);
  });
  ["acidity", "sweetness", "body", "aftertaste"].forEach((key) => {
    const value = clampDot(state.rate[key]);
    document.querySelectorAll(`[data-sense="${key}"]`).forEach((btn) => {
      btn.classList.toggle("on", Number(btn.dataset.value) <= value);
    });
  });
  const preview = $("#sensory-preview");
  if (preview) {
    const wrap = document.createElement("div");
    wrap.innerHTML = liveSensoryCard();
    preview.replaceWith(wrap.firstElementChild);
  }
}

function authView() {
  const providers = state.config?.providers || {};
  const signup = state.authMode === "register";
  return `
    <section class="flex min-h-dvh flex-col bg-cream px-5 pb-8 pt-12">
      <div class="rounded-2xl bg-gradient-to-br from-espresso via-[#4a3328] to-terracotta px-5 py-7 text-cream shadow-lg">
        <p class="text-[11px] font-semibold uppercase tracking-[0.18em] text-cream/70">BeanNote</p>
        <h1 class="font-display mt-1 text-3xl font-bold">${t("app_name")}</h1>
        <p class="mt-1 text-sm text-cream/80">${t("auth_tagline")}</p>
      </div>
      <div class="mt-6 space-y-3">
        <button type="button" data-oauth="google" class="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-white px-4 text-sm font-semibold text-espresso shadow-sm ring-1 ring-latte ${providers.google ? "" : "opacity-80"}">
          <svg viewBox="0 0 24 24" class="h-5 w-5" aria-hidden="true"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09Z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23Z"/><path fill="#FBBC05" d="M5.84 14.09A6.97 6.97 0 0 1 5.5 12c0-.72.12-1.42.34-2.09V7.07H2.18A11 11 0 0 0 1 12c0 1.78.43 3.46 1.18 4.93l3.66-2.84Z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53Z"/></svg>
          ${t("continue_google")}
        </button>
        <button type="button" data-oauth="apple" class="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-espresso px-4 text-sm font-semibold text-cream ${providers.apple ? "" : "opacity-80"}">
          <svg viewBox="0 0 24 24" class="h-5 w-5 fill-current" aria-hidden="true"><path d="M16.4 12.6c0-2.3 1.9-3.4 2-3.5-1.1-1.6-2.8-1.8-3.4-1.8-1.4-.2-2.8.9-3.5.9s-1.8-.8-3-.8c-1.5 0-3 .9-3.8 2.3-1.6 2.8-.4 7 1.2 9.3.8 1.1 1.7 2.3 2.9 2.3 1.1 0 1.6-.7 3-.7s1.8.7 3 .7 2-.1 2.9-2.2c.7-1 1-2 1-2.1-.1 0-2.3-.9-2.3-3.4ZM14.7 6.2c.6-.8 1.1-1.8.9-2.9-1 .1-2.1.7-2.8 1.5-.6.7-1.2 1.8-1 2.8 1.1.1 2.2-.5 2.9-1.4Z"/></svg>
          ${t("continue_apple")}
        </button>
      </div>
      <div class="my-6 flex items-center gap-3 text-xs uppercase tracking-wider text-muted">
        <span class="h-px flex-1 bg-latte"></span>${t("or_divider")}<span class="h-px flex-1 bg-latte"></span>
      </div>
      <form id="auth-form" class="space-y-3">
        <p class="text-center text-sm font-semibold">${t("login_email")}</p>
        ${signup ? `<input name="username" autocomplete="username" class="min-h-12 w-full rounded-xl border border-latte bg-white px-3" placeholder="${esc(t("username"))}">` : ""}
        <input name="email" type="email" required autocomplete="email" class="min-h-12 w-full rounded-xl border border-latte bg-white px-3" placeholder="${esc(t("email"))}">
        <input name="password" type="password" required minlength="8" autocomplete="${signup ? "new-password" : "current-password"}" class="min-h-12 w-full rounded-xl border border-latte bg-white px-3" placeholder="${esc(t("password"))}">
        <button class="min-h-12 w-full rounded-xl bg-terracotta text-sm font-semibold text-cream">${signup ? t("register") : t("login")}</button>
      </form>
      <button id="toggle-auth" class="mt-4 text-center text-sm text-muted">
        ${signup ? t("have_account") + " " + t("login") : t("no_account") + " " + t("register")}
      </button>
      <div class="mt-8 flex justify-center">${langToggle()}</div>
    </section>`;
}

function header() {
  const explore = state.tab === "explore" ? exploreToolbar() : "";
  return `<header class="header">
    <div class="header-row">
      <h1 class="header-title">${t("app_name")}</h1>
      <p class="header-ver">v${esc(state.config?.version || "")}</p>
    </div>
    ${explore}
  </header>`;
}

function exploreToolbar() {
  return `<div class="explore-toolbar">
    <div class="explore-search-row">
      <input id="search" value="${esc(state.search)}" class="explore-search" data-i18n-placeholder="search" placeholder="${esc(t("search"))}">
      ${exploreSortSelect()}
    </div>
    ${suitabilityBar()}
    ${exploreSegBar()}
  </div>`;
}

function sortOptionDefs() {
  const tpl = document.getElementById("sort-beans-select");
  if (tpl) {
    return [...tpl.content.querySelectorAll("option")].map((opt) => ({
      value: opt.value || "newest",
      key: opt.getAttribute("data-i18n") || "",
      fallback: (opt.textContent || "").trim(),
    }));
  }
  return [
    { value: "rating_desc", key: "sort_rating_desc", fallback: "Bedste bedømmelse" },
    { value: "ratings_count_desc", key: "sort_ratings_count_desc", fallback: "Flest bedømmelser" },
    { value: "roaster_asc", key: "sort_roaster_asc", fallback: "Risteri (A-Å)" },
    { value: "name_asc", key: "sort_name_asc", fallback: "Navn (A-Å)" },
    { value: "newest", key: "sort_newest", fallback: "Nyeste" },
  ];
}

function exploreSortSelect() {
  const current = state.beanSort || "newest";
  const options = sortOptionDefs().map((opt) => {
    const label = opt.key ? t(opt.key) : opt.fallback;
    const selected = opt.value === current ? " selected" : "";
    return `<option value="${esc(opt.value)}" data-i18n="${esc(opt.key)}"${selected}>${esc(label)}</option>`;
  }).join("");
  return `<select id="sortBeansSelect" class="explore-sort" data-i18n-aria="sort_beans" aria-label="${esc(t("sort_beans"))}">${options}</select>`;
}

function suitabilityBar() {
  const chips = availableSuitableFilters();
  if (!chips.length) return "";
  const items = [["", "filter_suitable_all"], ...chips];
  return `<div class="explore-pills">
    ${items.map(([id, key]) => {
      const on = state.suitabilityFilter === id;
      return `<button type="button" data-suitable="${id}" class="explore-pill${on ? " is-on" : ""}">${esc(t(key))}</button>`;
    }).join("")}
  </div>`;
}

function suitabilityLine(tags) {
  const labels = parseSuitableFor(tags).map(localizeSuitable).filter(Boolean);
  if (!labels.length) return "";
  const chips = labels.map((label) =>
    `<span class="suitable-pill rounded-full bg-cream px-2.5 py-1 text-xs font-semibold text-terracotta ring-1 ring-terracotta">${esc(label)}</span>`
  ).join("");
  return `<div class="inline-flex max-w-full flex-wrap items-center gap-1.5">
    <span class="text-sm font-semibold text-espresso">${esc(t("suitable_for"))}:</span>
    ${chips}
  </div>`;
}

function localizeBrewMethod(method) {
  const raw = String(method || "").trim();
  if (!raw) return "";
  const table = state.config?.brew_method_i18n || {};
  const hit = table[raw] || table[raw.toLowerCase()];
  return (hit && (hit[activeLang()] || hit.en || hit.da)) || raw;
}

function supportEnabled() {
  return !!(state.config?.support_enabled && (state.config.mobilepay_url || state.config.buymeacoffee_url));
}

function supportButton(extra = "") {
  if (!supportEnabled()) return "";
  return `<button type="button" data-open-support class="flex min-h-12 w-full items-center justify-center rounded-xl bg-white font-semibold text-espresso ring-1 ring-latte ${extra}" data-i18n="support_app">${esc(t("support_app"))}</button>`;
}

function supportModal() {
  if (!state.supportOpen || !supportEnabled()) return "";
  const testNote = state.config?.support_test_mode
    ? `<p class="mt-3 text-center text-xs font-semibold text-terracotta" data-i18n="support_test_mode">${esc(t("support_test_mode"))}</p>`
    : "";
  const mobile = state.config.mobilepay_url
    ? `<a href="${esc(state.config.mobilepay_url)}" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center rounded-xl bg-terracotta font-semibold text-cream" data-i18n="support_mobilepay">${esc(t("support_mobilepay"))}</a>`
    : "";
  const coffee = state.config.buymeacoffee_url
    ? `<a href="${esc(state.config.buymeacoffee_url)}" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center rounded-xl bg-espresso font-semibold text-cream" data-i18n="support_buymeacoffee">${esc(t("support_buymeacoffee"))}</a>`
    : "";
  return `<div id="support-modal" data-close-support class="fixed inset-0 z-50 flex items-end justify-center bg-espresso/50 px-4 sm:items-center">
    <article class="mb-20 w-full max-w-sm overflow-hidden rounded-3xl bg-cream shadow-2xl sm:mb-0" data-support-sheet>
      <div class="coffee-pattern px-5 py-6 text-cream">
        <p class="text-[11px] font-semibold uppercase tracking-[0.16em] text-cream/70">BeanNote</p>
        <h2 class="font-display mt-1 text-2xl font-bold" data-i18n="support_title">${esc(t("support_title"))}</h2>
      </div>
      <div class="space-y-3 p-5">
        <p class="text-sm leading-6 text-espresso/80" data-i18n="support_sub">${esc(t("support_sub"))}</p>
        <div class="grid gap-2">${mobile}${coffee}</div>
        ${testNote}
        <button type="button" data-close-support class="min-h-11 w-full text-sm font-semibold text-muted" data-i18n="close_detail">${esc(t("close_detail"))}</button>
      </div>
    </article>
  </div>`;
}

function exploreView() {
  const empty = state.beanFilter === "favorites" ? t("no_favorites") : t("empty_explore");
  const mapMode = state.exploreMode === "map";
  const cards = visibleBeans().map((bean) => {
    const photo = photoImg(bean.image_url, bean.snapshot_url, "bean-card-img");
    return `<article class="bean-card">
      ${heartBtn(bean, "absolute right-2 top-2 z-10")}
      <button data-open-bean="${bean.id}" class="w-full text-left">
        <div class="bean-card-img-wrap bean-card-photo">${photo || bagFallback("h-full")}${roastBadge(bean)}</div>
        <div class="p-3">
          <p class="font-display text-lg font-semibold">${esc(bean.name)}</p>
          <p class="text-sm text-muted">${beanCardMeta(bean)}</p>
          <div class="mt-2 flex items-center justify-between gap-2">
            <div class="flex min-w-0 flex-wrap gap-1">${feedPills(bean.flavor_tags)}</div>
            <p class="shrink-0 text-sm font-semibold text-amber-600">★ ${(bean.avg_rating || 0).toFixed(1)}</p>
          </div>
        </div>
      </button>
    </article>`;
  }).join("");
  if (mapMode) {
    return `<section class="explore-map">
      <div id="world-map" class="w-full overflow-hidden rounded-xl ring-1 ring-latte"></div>
      ${supportEnabled() ? `<div class="mt-3 shrink-0">${supportButton()}</div>` : ""}
      ${state.profile?.bean ? beanModal(state.profile) : ""}
    </section>`;
  }
  return `<section class="explore-list">
    <div class="grid gap-3">${cards || `<p class="text-sm text-muted">${empty}</p>`}</div>
    ${supportEnabled() ? `<div class="mt-5">${supportButton()}</div>` : ""}
    ${state.profile?.bean ? beanModal(state.profile) : ""}
  </section>`;
}

function recipeMeta(row) {
  const bits = [];
  if (row.grind_setting) bits.push(t("recipe_grind_value", { grind: row.grind_setting }));
  const coffee = formatGramsLabel(row.coffee_grams);
  const water = formatGramsLabel(row.water_grams);
  if (coffee && water) {
    bits.push(t("recipe_dose_value", { coffee, water }));
  } else {
    if (coffee) bits.push(`☕ ${coffee}g`);
    if (water) bits.push(`💧 ${water}g`);
  }
  if (row.brew_time) bits.push(`⏱️ ${normalizeBrewTime(row.brew_time)}`);
  return bits;
}

function gearLogBadges(row) {
  const bits = [];
  if (row.espresso_machine) bits.push(`☕ ${row.espresso_machine}`);
  if (row.grinder) bits.push(`⚙️ ${row.grinder}`);
  if (!bits.length) return "";
  return `<div class="mt-1.5 flex flex-wrap gap-1">${bits.map((item) =>
    `<span class="rounded-full bg-foam px-2 py-0.5 text-[11px] font-semibold text-espresso">${esc(item)}</span>`
  ).join("")}</div>`;
}

function foldGearName(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function gearNamesMatch(left, right) {
  const a = foldGearName(left);
  const b = foldGearName(right);
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length <= b.length ? b : a;
  return shorter.length >= 6 && longer.includes(shorter);
}

function matchesGearList(name, candidates) {
  return (candidates || []).some((item) => gearNamesMatch(name, item));
}

function recipeGearMatch(row) {
  const known = String(row?.gear_match || "");
  if (known === "exact" || known === "machine" || known === "grinder") return known;
  const brewers = [...gearItemsOfKind("espresso_machine"), ...gearItemsOfKind("brewer")];
  const grinders = gearItemsOfKind("grinder");
  const machineHit = brewers.length > 0 && matchesGearList(row?.espresso_machine, brewers);
  const grinderHit = grinders.length > 0 && matchesGearList(row?.grinder, grinders);
  if (machineHit && grinderHit) return "exact";
  if (machineHit) return "machine";
  if (grinderHit) return "grinder";
  return "";
}

function communityRecipesForDisplay(rows) {
  const annotated = (rows || []).map((row) => ({ ...row, gear_match: recipeGearMatch(row) }));
  const rank = { exact: 0, machine: 1, grinder: 2 };
  const hits = annotated.filter((row) => row.gear_match);
  if (hits.length) {
    hits.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    hits.sort((a, b) => (rank[a.gear_match] ?? 9) - (rank[b.gear_match] ?? 9));
    return { rows: hits, fallback: false };
  }
  return { rows: annotated.map((row) => ({ ...row, gear_match: "" })), fallback: true };
}

function gearMatchBadge(row) {
  const tier = row?.gear_match;
  const key = { exact: "gear_match_exact", machine: "gear_match_machine", grinder: "gear_match_grinder" }[tier];
  if (!key) return "";
  return `<span class="gear-match-badge gear-match-${esc(tier)}" data-i18n="${key}">${esc(t(key))}</span>`;
}

function officialScores(source) {
  const profile = source?.roaster_profile && typeof source.roaster_profile === "object"
    ? source.roaster_profile
    : {};
  const bean = source?.bean || source || {};
  return {
    acidity: profile.acidity ?? bean.roaster_acidity ?? bean.acidity_score,
    body: profile.body ?? bean.roaster_body ?? bean.body_score,
    roast: profile.roast_level ?? bean.roaster_roast_level ?? bean.roast_level_score,
  };
}

function scoreDots(score, variant = "") {
  const n = Number(score);
  const value = Number.isFinite(n) ? Math.max(0, Math.min(5, n)) : 0;
  const rounded = Math.round(value);
  const display = Number.isInteger(value) || Math.abs(value - rounded) < 0.05
    ? String(rounded)
    : value.toFixed(1);
  const klass = variant ? `flavor-dot ${variant}` : "flavor-dot";
  const dots = [1, 2, 3, 4, 5].map((i) =>
    `<span class="${klass}${i <= rounded ? " on" : ""}" aria-hidden="true"></span>`
  ).join("");
  return { value, display, dots, rounded };
}

function recommendedBrewLine(source) {
  const rec = brewSource(source?.bean || source);
  const ratioTemp = [rec.ratio, rec.temp].filter(Boolean).join(" / ");
  const methodGrind = [rec.method, rec.grind].filter(Boolean).join(" · ");
  return { ratioTemp, methodGrind, usage: rec.usage || "", has: !!(ratioTemp || methodGrind || rec.usage) };
}

function roasterProfileCard(profile) {
  const bean = profile?.bean || profile || {};
  const scores = officialScores(profile);
  const rows = [
    ["roaster_target_acidity", scores.acidity],
    ["roaster_target_body", scores.body],
    ["roaster_target_roast", scores.roast],
  ].filter(([, score]) => Number(score) > 0);
  const brew = recommendedBrewLine(profile);
  if (!rows.length && !brew.has) return "";
  const meters = rows.map(([key, score]) => {
    const { display, dots, value } = scoreDots(score, "target");
    return `<div class="flex items-center justify-between gap-3">
      <p class="min-w-[7.5rem] shrink-0 text-sm font-semibold" data-i18n="${key}">${esc(t(key))}</p>
      <div class="flex items-center gap-2" role="meter" aria-valuemin="0" aria-valuemax="5" aria-valuenow="${value}" aria-label="${esc(t(key))}">
        <div class="flex items-center gap-1">${dots}</div>
        <p class="text-xs font-semibold tabular-nums text-terracotta">${display}/5</p>
      </div>
    </div>`;
  }).join("");
  return `<section class="space-y-2">
    <p class="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted" data-i18n="roaster_profile">${esc(t("roaster_profile"))}</p>
    <div class="roaster-card space-y-3 rounded-2xl px-3 py-3 shadow-sm">
      <h3 class="text-sm font-bold text-espresso">🏷️ <span data-i18n="roaster_profile_title">${esc(t("roaster_profile_title"))}</span></h3>
      ${meters ? `<div class="space-y-1.5">${meters}</div>` : ""}
      ${brew.has ? `<div class="rounded-xl bg-cream/80 px-3 py-2">
        <p class="text-sm font-semibold">💡 <span data-i18n="recommended_recipe">${esc(t("recommended_recipe"))}</span></p>
        ${brew.usage ? `<p class="mt-1 text-sm leading-6 text-espresso/80">${esc(brew.usage)}</p>` : ""}
        ${brew.ratioTemp ? `<p class="mt-0.5 text-sm font-semibold text-espresso">${esc(brew.ratioTemp)}</p>` : ""}
        ${brew.methodGrind ? `<p class="text-xs text-muted">${esc(brew.methodGrind)}</p>` : ""}
      </div>` : ""}
    </div>
  </section>`;
}

function compareSenseRow(labelKey, youScore, targetScore) {
  const you = scoreDots(youScore, "you");
  const target = scoreDots(targetScore, "target");
  const showYou = Number(youScore) > 0;
  const showTarget = Number(targetScore) > 0;
  if (!showYou && !showTarget) return "";
  const line = (variant, labelKeyInner, pack, show) => show ? `<div class="flex items-center justify-between gap-3 pl-1">
    <p class="min-w-[4.5rem] text-xs font-semibold text-muted" data-i18n="${labelKeyInner}">${esc(t(labelKeyInner))}</p>
    <div class="flex items-center gap-2">
      <div class="flex items-center gap-1">${pack.dots}</div>
      <p class="text-xs font-semibold tabular-nums ${variant === "you" ? "text-espresso" : "text-terracotta"}">${pack.display}/5</p>
    </div>
  </div>` : "";
  return `<div class="space-y-1">
    <p class="text-sm font-semibold" data-i18n="${labelKey}">${esc(t(labelKey))}</p>
    ${line("you", "you_label", you, showYou)}
    ${line("target", "roaster_label", target, showTarget)}
  </div>`;
}

function latestTastingCard(profile) {
  const user = profile?.user;
  if (!user) {
    return `<div class="personal-card rounded-2xl px-3 py-3 shadow-sm">
      <h3 class="text-sm font-bold">✍️ <span data-i18n="your_tasting">${esc(t("your_tasting"))}</span></h3>
      <p class="mt-2 text-sm text-muted" data-i18n="no_ratings">${esc(t("no_ratings"))}</p>
    </div>`;
  }
  const method = localizeBrewMethod(user.brew_method);
  const date = (user.created_at || "").slice(0, 10);
  const header = [`⭐ ${Number(user.rating || 0).toFixed(1)}`];
  if (method) header.push(`☕ ${method}`);
  if (date) header.push(date);
  const official = officialScores(profile);
  const notes = String(user.tasting_notes_user || user.notes || "").trim();
  const compare = [
    compareSenseRow("sense_acidity", user.acidity, official.acidity),
    compareSenseRow("sense_body", user.body, official.body),
  ].filter(Boolean).join("");
  const extras = sensoryStrip({
    sweetness: user.sweetness,
    aftertaste: user.aftertaste,
  });
  return `<div class="personal-card space-y-2.5 rounded-2xl px-3 py-3 shadow-sm">
    <h3 class="text-sm font-bold">✍️ <span data-i18n="your_tasting">${esc(t("your_tasting"))}</span></h3>
    <p class="text-sm font-semibold text-espresso">${esc(header.join(" · "))}</p>
    ${gearLogBadges(user)}
    ${compare ? `<div class="space-y-2">${compare}</div>` : ""}
    ${extras}
    ${notes ? `<p class="text-sm leading-5 text-espresso/80">💬 "${esc(notes)}"</p>` : ""}
  </div>`;
}

function personalLogSection(profile) {
  return `<section class="space-y-3">
    <p class="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted" data-i18n="personal_log">${esc(t("personal_log"))}</p>
    ${latestTastingCard(profile)}
    ${recipeLogSection(profile)}
  </section>`;
}

function recipeCard(row) {
  const method = localizeBrewMethod(row.brew_method);
  const date = (row.created_at || "").slice(0, 10);
  const header = [`⭐ ${Number(row.rating || 0).toFixed(1)}`];
  if (method) header.push(`☕ ${method}`);
  if (date) header.push(date);
  const badges = recipeMeta(row);
  const notes = String(row.tasting_notes_user || row.notes || "").trim();
  const match = gearMatchBadge(row);
  return `<article class="rounded-2xl bg-white px-3 py-2.5 shadow-sm ring-1 ring-latte">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <p class="text-sm font-semibold text-espresso">${esc(header.join(" · "))}</p>
      ${match}
    </div>
    ${badges.length ? `<p class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-espresso/80">${badges.map((item) => `<span>${esc(item)}</span>`).join(`<span class="text-latte">|</span>`)}</p>` : ""}
    ${gearLogBadges(row)}
    ${notes ? `<p class="mt-1.5 text-sm leading-5 text-espresso/80">💬 "${esc(notes)}"</p>` : ""}
  </article>`;
}

function tastingHistory(history) {
  const rows = history || [];
  if (!rows.length) return "";
  return rows.map(recipeCard).join("");
}

function recipeLogSection(profile) {
  const tab = state.recipeTab === "community" ? "community" : "mine";
  const mine = profile?.history || [];
  let rows = mine;
  let banner = "";
  if (tab === "community") {
    const shown = communityRecipesForDisplay(profile?.community_history || []);
    rows = shown.rows;
    if (shown.fallback && rows.length) {
      banner = `<p class="rounded-2xl bg-foam px-3 py-2 text-xs font-semibold text-espresso ring-1 ring-latte" data-i18n="community_all_recipes">${esc(t("community_all_recipes"))}</p>`;
    }
  }
  const emptyKey = tab === "community" ? "community_empty" : "no_ratings";
  const tabBtn = (id, key) => {
    const on = tab === id;
    return `<button type="button" data-recipe-tab="${id}" class="min-h-10 flex-1 rounded-lg px-2 text-sm font-semibold ${on ? "bg-white text-espresso shadow-sm" : "text-muted"}" data-i18n="${key}">${esc(t(key))}</button>`;
  };
  return `<section class="space-y-2">
    <p class="text-sm font-semibold" data-i18n="recipe_log">${esc(t("recipe_log"))}</p>
    <div class="flex gap-1 rounded-xl bg-foam p-1" role="tablist">
      ${tabBtn("mine", "my_recipes")}
      ${tabBtn("community", "community_recipes")}
    </div>
    ${banner}
    ${rows.length ? tastingHistory(rows) : `<p class="rounded-2xl bg-white px-3 py-3 text-sm text-muted shadow-sm ring-1 ring-latte" data-i18n="${emptyKey}">${esc(t(emptyKey))}</p>`}
  </section>`;
}

function retailerSearchHref(bean) {
  const query = `"${bean.roaster || ""}" "${bean.name || ""}" køb buy`;
  return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

function safeRoasterHref(url) {
  const raw = String(url || "").trim();
  if (!/^https?:\/\//i.test(raw)) return "";
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return "";
    const host = (parsed.hostname || "").toLowerCase();
    if (!host || host === "localhost" || host.endsWith(".local")) return "";
    parsed.protocol = "https:";
    return parsed.href;
  } catch {
    return "";
  }
}

function retailerActions(bean) {
  const search = retailerSearchHref(bean);
  const site = safeRoasterHref(bean.roaster_url);
  const visit = site
    ? `<a href="${esc(site)}" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center rounded-xl bg-espresso font-semibold text-cream" data-i18n="visit_roaster">${t("visit_roaster")}</a>`
    : "";
  return `<div class="grid gap-2">
    ${visit}
    <a href="${esc(search)}" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center rounded-xl bg-foam font-semibold ring-1 ring-latte" data-i18n="find_retailer">${t("find_retailer")}</a>
  </div>`;
}

function beanModal(profile) {
  const bean = profile.bean;
  const rating = state.rateOpen;
  const photo = photoImg(bean.image_url, bean.snapshot_url, "modal-cover-img", ' id="modalCoverImg"');
  const info = [bean.roaster, bean.origin, bean.process, bean.roast_level].filter(Boolean).join(" · ");
  const editor = isAdmin() && state.editBean ? scanEditor(bean) : "";
  const flavorPills = pills(bean.flavor_tags);
  let suitableFor = [];
  try {
    suitableFor = Array.isArray(bean.suitable_for) ? bean.suitable_for : JSON.parse(bean.suitable_for || "[]");
  } catch {
    suitableFor = [];
  }
  const suitableLine = suitabilityLine(suitableFor);
  const extras = rating ? "" : `${retailerActions(bean)}
          ${flavorPills || parseSuitableFor(suitableFor).length ? `<div class="flex flex-wrap items-center gap-1.5">${flavorPills}${suitableLine}</div>` : ""}
          ${metaBadges(bean)}
          ${storyBlock(bean.story)}`;
  const footer = rating ? "" : `${roasterProfileCard(profile)}
        ${personalLogSection(profile)}
        <section>
          ${originMapBox(bean)}
        </section>
        <button type="button" data-enrich-bean class="min-h-12 w-full rounded-xl bg-foam font-semibold ring-1 ring-latte" data-i18n="enrich_bean">${t("enrich_bean")}</button>
        ${isAdmin() ? `<button id="toggle-bean-edit" class="min-h-11 w-full text-sm font-semibold text-muted">${t("edit_details")}</button>` : ""}
        ${editor}
        ${isAdmin() && state.editBean ? `<button type="button" data-enrich-bean class="min-h-12 w-full rounded-xl bg-foam font-semibold ring-1 ring-latte" data-i18n="enrich_bean">${t("enrich_bean")}</button>` : ""}
        ${isAdmin() && state.editBean ? `<button id="save-masterdata" class="min-h-12 w-full rounded-xl bg-espresso font-semibold text-cream">${t("save_masterdata")}</button>` : ""}`;
  return `<div id="bean-modal" data-close-modal class="fixed inset-0 z-40 flex items-end justify-center bg-espresso/50 px-0 sm:items-center sm:px-4${rating ? " rating-focus" : ""}">
    <article class="relative max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-3xl bg-cream shadow-2xl sm:rounded-3xl" data-modal-sheet>
      <div class="modal-close-bar">
        <button type="button" data-close-modal class="grid h-10 w-10 place-items-center rounded-full bg-cream/95 text-lg font-semibold shadow" data-i18n-aria="close_detail" aria-label="${esc(t("close_detail"))}">✕</button>
      </div>
      <div class="modal-cover rounded-t-3xl sm:rounded-t-3xl">
        ${photo || bagFallback(rating ? "h-28" : "h-56")}
        ${heartBtn(bean, "modal-cover-fav absolute left-3 top-3 z-10")}
      </div>
      <div class="space-y-4 p-4 ${rating ? "pb-6" : "pb-8"}">
        <section class="space-y-3">
          <h2 class="font-display text-2xl font-bold">${esc(bean.name)}</h2>
          <p class="text-sm text-muted">${esc(info)}</p>
          ${rating ? "" : `<button type="button" id="open-rate-form" class="flex min-h-12 w-full items-center justify-center rounded-xl bg-terracotta font-semibold text-cream" data-i18n="rate_this_bean">${t("rate_this_bean")}</button>`}
          ${rating ? rateForm() : ""}
          ${extras}
        </section>
        ${footer}
      </div>
    </article>
  </div>`;
}

function scanView() {
  const scan = state.scan;
  if (!scan) {
    return `<section class="px-4 pb-28 pt-6">
      <div class="rounded-2xl border-2 border-dashed border-terracotta/50 bg-white p-6 text-center">
        <h2 class="font-display text-xl font-bold" data-i18n="scan_card_title">${t("scan_card_title")}</h2>
        <p class="mt-1 text-sm text-muted" data-i18n="scan_card_sub">${t("scan_card_sub")}</p>
        <button type="button" id="pick-scan" class="mt-5 flex min-h-12 w-full items-center justify-center rounded-xl bg-terracotta font-semibold text-cream" data-i18n="scan_pick_image">${t("scan_pick_image")}</button>
      </div>
    </section>`;
  }
  const preview = photoImg(scan.image_url, scan.preview || scan.snapshot_url, "max-h-72 w-full object-contain bg-foam")
    || (scan.preview ? `<img src="${esc(scan.preview)}" alt="" class="max-h-72 w-full object-contain bg-foam">` : "");
  const similar = scan.similar?.[0];
  const existing = existingScanMatch(scan);
  const canEdit = isAdmin();
  const fields = canEdit && state.editScan ? scanEditor(scan) : "";
  return `<section class="px-4 pb-28 pt-4">
    <article class="overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-latte">
      <div id="scan-cover-hero">${preview || bagFallback()}</div>
      <div class="space-y-3 p-4">
        ${coverPicker(scan)}
        <p class="text-xs font-semibold uppercase tracking-wider text-muted">${t("ai_summary_title")}</p>
        <h2 class="font-display text-2xl font-bold">${esc(scan.name || t("add_name"))}</h2>
        <p class="text-sm text-muted">${esc(scan.roaster)} · ${esc(scan.origin)} · ${esc(scan.process)}</p>
        <div class="flex flex-wrap gap-1">${pills(scan.flavor_tags)}</div>
        ${suitabilityLine(scan.suitable_for)}
        ${metaBadges(scan)}
        ${originMapBox(scan)}
        ${journalBits(scan)}
        ${existing ? `<div class="rounded-xl bg-[#f4ebd9] p-3 text-sm ring-1 ring-latte">
          <p class="font-semibold" data-i18n="bean_in_archive">${t("bean_in_archive")}</p>
          <p class="mt-1 text-muted">${esc(existing.name)} · ${esc(existing.roaster)}</p>
          <button type="button" data-open-archive="${esc(existing.id)}" class="mt-2 min-h-11 w-full rounded-lg bg-terracotta font-semibold text-cream" data-i18n="open_and_rate">${t("open_and_rate")}</button>
        </div>
        <button type="button" id="undo-scan" class="min-h-12 w-full rounded-xl bg-foam font-semibold ring-1 ring-latte" data-i18n="undo_rescan">${t("undo_rescan")}</button>` : `${similar ? `<div class="rounded-xl bg-foam p-3 text-sm">
          <p class="font-semibold">${t("duplicate_warning")}</p>
          <p class="mt-1 text-muted">${esc(similar.name)} · ${esc(similar.roaster)} (${Math.round((similar.confidence || 0) * 100)}% ${t("confidence")})</p>
          <button data-rate-bean="${similar.id}" class="mt-2 min-h-11 w-full rounded-lg bg-espresso text-cream">${t("use_existing")}</button>
        </div>` : ""}
        <div class="flex flex-col gap-2">
          <button id="approve-bean" class="min-h-12 w-full rounded-xl bg-terracotta font-semibold text-cream" data-i18n="approve_save">${t("approve_save")}</button>
          <button type="button" id="undo-scan" class="min-h-12 w-full rounded-xl bg-foam font-semibold ring-1 ring-latte" data-i18n="undo_rescan">${t("undo_rescan")}</button>
        </div>`}
        ${canEdit ? `<button id="toggle-edit" class="min-h-11 w-full text-sm font-semibold text-muted">${t("edit_details")}</button>` : ""}
        ${fields}
      </div>
    </article>
  </section>`;
}

function coverOptions(scan) {
  if (!scan) return [];
  const snapshot = scan.snapshot_url || scan.preview || "";
  const studios = (scan.image_candidates || []).filter((url) => url && url !== snapshot);
  const options = [];
  if (snapshot) {
    options.push({ url: snapshot, thumb: scan.preview || snapshot, kind: "own" });
  }
  studios.forEach((url, index) => {
    options.push({ url, thumb: url, kind: "studio", n: index + 1 });
  });
  return options;
}

function coverPicker(scan) {
  const options = coverOptions(scan);
  if (!options.length) return "";
  const selected = scan.image_url || options[0].url;
  const thumbs = options.map((option) => {
    const on = option.url === selected;
    const label = option.kind === "own"
      ? t("cover_your_photo")
      : t("cover_studio_photo", { n: option.n });
    const img = photoImg(option.thumb, option.url, "h-[4.4rem] w-full rounded-lg object-cover bg-foam")
      || bagThumb();
    return `<button type="button" data-cover-url="${esc(option.url)}" aria-pressed="${on ? "true" : "false"}" class="cover-choice">
      ${img}
      <span class="mt-1 block text-[10px] font-semibold leading-tight ${on ? "text-terracotta" : "text-muted"}">${esc(label)}</span>
    </button>`;
  }).join("");
  const usingOwn = selected === (scan.snapshot_url || options[0].url);
  return `<div class="space-y-2 rounded-xl bg-foam p-3">
    <p class="text-xs font-semibold uppercase tracking-wider text-muted">${t("cover_picker_title")}</p>
    <div class="cover-carousel" role="listbox" aria-label="${esc(t("cover_picker_title"))}">${thumbs}</div>
    <button type="button" id="use-own-photo" class="min-h-11 w-full rounded-lg bg-white text-sm font-semibold ring-1 ring-latte ${usingOwn ? "opacity-50" : ""}"${usingOwn ? " disabled" : ""}>${t("cover_use_own")}</button>
  </div>`;
}

function selectScanCover(url) {
  if (!state.scan || !url) return;
  const rail = $(".cover-carousel");
  const left = rail ? rail.scrollLeft : 0;
  state.scan.image_url = url;
  render();
  const next = $(".cover-carousel");
  if (next) next.scrollLeft = left;
}

function scanEditor(scan) {
  const options = (list, selected) => {
    const items = ["", ...(list || [])];
    if (selected && !items.includes(selected)) items.push(selected);
    return items.map((item) =>
      `<option value="${esc(item)}" ${item === selected ? "selected" : ""}>${item || "—"}</option>`
    ).join("");
  };
  return `<form id="scan-edit" class="space-y-2 rounded-xl bg-foam p-3">
    <input name="name" value="${esc(scan.name || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("add_name"))}">
    <input name="roaster" value="${esc(scan.roaster || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("add_roaster"))}">
    <input name="roaster_url" value="${esc(scan.roaster_url || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("add_roaster_url"))}" inputmode="url">
    <input name="origin" value="${esc(scan.origin || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("origin"))}">
    <input name="region_full" value="${esc(scan.region_full || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("region_full"))}">
    <input name="roast_date" value="${esc(scan.roast_date || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("roast_date"))}">
    <input name="altitude" value="${esc(scan.altitude || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("altitude"))}">
    <input name="varietal" value="${esc(scan.varietal || "")}" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3" placeholder="${esc(t("varietal"))}">
    <select name="process" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3">${options(state.config.processes, scan.process)}</select>
    <select name="roast_level" class="min-h-11 w-full rounded-lg border border-latte bg-white px-3">${options(state.config.roast_levels, scan.roast_level)}</select>
    <textarea name="roaster_notes" class="min-h-20 w-full rounded-lg border border-latte bg-white px-3 py-2" placeholder="${esc(t("add_roaster_notes"))}">${esc(scan.roaster_notes || "")}</textarea>
    <textarea name="story" class="min-h-24 w-full rounded-lg border border-latte bg-white px-3 py-2" placeholder="${esc(t("bean_story"))}">${esc(getLocalized(scan.story) || "")}</textarea>
  </form>`;
}

function gearItemsOfKind(kind) {
  const gear = userGear();
  const names = [];
  const push = (name) => {
    const clean = String(name || "").trim();
    if (clean && !names.includes(clean)) names.push(clean);
  };
  if (kind === "espresso_machine") push(gear.espresso_machine);
  if (kind === "grinder") push(gear.grinder);
  (gear.gear_specs || []).forEach((item) => {
    if (gearKindOf(item) === kind) push(gearNameOf(item));
  });
  return names;
}

function methodFromGearName(name) {
  const hay = String(name || "").toLowerCase();
  if (!hay) return "";
  const methods = state.config?.brew_methods || [];
  const aliases = [
    ["aeropress", "AeroPress"],
    ["chemex", "Chemex"],
    ["french press", "French Press"],
    ["stempel", "French Press"],
    ["kalita", "Kalita"],
    ["moka", "Moka"],
    ["cold brew", "Cold Brew"],
    ["batch", "Batch Brew"],
    ["v60", "V60"],
    ["espresso", "Espresso"],
  ];
  const canon = (method) => {
    if (!method) return "";
    if (!methods.length || methods.includes(method)) return method;
    return methods.find((item) => item.toLowerCase() === method.toLowerCase()) || "";
  };
  for (const [needle, method] of aliases) {
    if (hay.includes(needle)) return canon(method);
  }
  for (const method of methods) {
    if (hay.includes(method.toLowerCase())) return method;
  }
  return "";
}

function impliedBrewMethod() {
  const machine = String(state.rate.espresso_machine || "").trim();
  if (machine) return methodFromGearName(machine) || "Espresso";
  const brewers = gearItemsOfKind("brewer");
  if (brewers.length === 1) return methodFromGearName(brewers[0]);
  return "";
}

function renderGearControl(kind, id, valueKey, ariaKey) {
  const registered = gearItemsOfKind(kind);
  const selected = String(state.rate[valueKey] || "").trim();
  const names = registered.slice();
  if (selected && !names.includes(selected)) names.push(selected);
  if (!names.length) return "";
  if (names.length === 1) {
    return `<div>
              <span class="gear-badge">${esc(names[0])}</span>
              <input type="hidden" id="${id}" value="${esc(names[0])}">
            </div>`;
  }
  const opts = [`<option value="">${esc(t("gear_none"))}</option>`]
    .concat(names.map((name) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`));
  return `<select id="${id}" class="min-h-11 w-full rounded-xl border border-latte bg-white px-2 text-sm" aria-label="${esc(t(ariaKey))}">${opts.join("")}</select>`;
}

function brewMethodControl() {
  const implied = impliedBrewMethod();
  const locked = !!(implied && !state.rate.brewMethodManual);
  if (locked) {
    return `<div>
              <button type="button" id="unlock-brew-method" class="gear-badge gear-badge-locked mt-1" aria-label="${esc(t("recipe_brew"))}">${esc(localizeBrewMethod(state.rate.brew_method))}</button>
              <input type="hidden" id="brew" value="${esc(state.rate.brew_method)}">
            </div>`;
  }
  const methods = (state.config?.brew_methods || []).map((m) =>
    `<option value="${esc(m)}" ${m === state.rate.brew_method ? "selected" : ""}>${esc(localizeBrewMethod(m))}</option>`
  ).join("");
  return `<select id="brew" class="mt-1 min-h-11 w-full rounded-xl border border-latte bg-white px-2 text-sm">${methods}</select>`;
}

function rateForm() {
  const machine = renderGearControl("espresso_machine", "rate_espresso_machine", "espresso_machine", "gear_machine");
  const grinder = renderGearControl("grinder", "rate_grinder", "grinder", "gear_grinder");
  const gearRow = (machine || grinder)
    ? `<label class="col-span-2 block text-xs font-semibold">
          <span data-i18n="gear_for_brew">${t("gear_for_brew")}</span>
          <div class="mt-1 grid ${machine && grinder ? "grid-cols-2" : "grid-cols-1"} gap-2">
            ${machine}${grinder}
          </div>
        </label>`
    : "";
  return `<div id="rate-form" class="space-y-3 rounded-2xl bg-white p-3 shadow-sm ring-1 ring-latte">
    ${starControl(state.rate.rating)}
    ${sensorySelectors()}
    <fieldset class="recipe-block rounded-2xl bg-foam p-3">
      <legend class="px-1 text-sm font-semibold" data-i18n="my_recipe">${t("my_recipe")}</legend>
      <div class="recipe-grid">
        <label class="block text-xs font-semibold">
          <span data-i18n="recipe_brew">${t("recipe_brew")}</span>
          ${brewMethodControl()}
        </label>
        <label class="block text-xs font-semibold">
          <span data-i18n="recipe_grind">${t("recipe_grind")}</span>
          <input id="grind_setting" value="${esc(state.rate.grind_setting || "")}" class="mt-1 min-h-11 w-full rounded-xl border border-latte bg-white px-3 text-sm" placeholder="${esc(t("grind_setting_ph"))}">
        </label>
        ${gearRow}
        <label class="block text-xs font-semibold">
          <span data-i18n="coffee_grams">${t("coffee_grams")}</span>
          <div class="relative mt-1">
            <input id="coffee_grams" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" lang="${esc(deviceLocale())}" pattern="[0-9]*[.,]?[0-9]*" value="${esc(formatGramsInput(state.rate.coffee_grams))}" class="grams-input min-h-11 w-full rounded-xl border border-latte bg-white px-3 pr-7 text-sm" placeholder="${esc(formatGramsInput(18.5))}">
            <span class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-muted">g</span>
          </div>
        </label>
        <label class="block text-xs font-semibold">
          <span data-i18n="water_grams">${t("water_grams")}</span>
          <div class="relative mt-1">
            <input id="water_grams" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" lang="${esc(deviceLocale())}" pattern="[0-9]*[.,]?[0-9]*" value="${esc(formatGramsInput(state.rate.water_grams))}" class="grams-input min-h-11 w-full rounded-xl border border-latte bg-white px-3 pr-7 text-sm" placeholder="${esc(formatGramsInput(288) || t("water_grams_ph"))}">
            <span class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-muted">g</span>
          </div>
        </label>
        <label class="col-span-2 block text-xs font-semibold">
          <span data-i18n="recipe_time">${t("recipe_time")}</span>
          ${brewTimeInput()}
        </label>
      </div>
    </fieldset>
    <label class="block text-sm font-medium"><span data-i18n="tasting_notes_user">${t("tasting_notes_user")}</span>
      <textarea id="notes" class="mt-1 min-h-24 w-full rounded-xl border border-latte bg-white px-3 py-2" placeholder="${esc(t("tasting_notes_user_ph"))}">${esc(state.rate.notes)}</textarea>
    </label>
    <button id="save-rating" class="min-h-12 w-full rounded-xl bg-terracotta font-semibold text-cream" data-i18n="save_rating">${t("save_rating")}</button>
  </div>`;
}

function userGear() {
  return {
    espresso_machine: state.user?.espresso_machine || "",
    grinder: state.user?.grinder || "",
    brewer_types: Array.isArray(state.user?.brewer_types) ? [...state.user.brewer_types] : [],
    gear_specs: Array.isArray(state.user?.gear_specs) ? [...state.user.gear_specs] : [],
  };
}

function gearKindOf(item) {
  const raw = String(item?.type || item?.kind || item?.gear_type || "other").toLowerCase().trim().replace(/[-\s]+/g, "_");
  if (["machine", "espresso", "espresso_machine"].includes(raw)) return "espresso_machine";
  if (["grinder", "mill"].includes(raw)) return "grinder";
  if (["brewer", "brew", "filter"].includes(raw)) return "brewer";
  if (["scale_kettle", "scale", "kettle", "scales"].includes(raw)) return "scale_kettle";
  return raw || "other";
}

const GEAR_SLOTS = ["espresso_machine", "grinder", "brewer", "scale_kettle"];
const GEAR_KIND_FALLBACKS = {
  gear_scale_kettle: { da: "Vægt & Kedel", en: "Scale & Kettle", de: "Waage & Kessel", fr: "Balance & bouilloire", es: "Báscula y hervidor" },
};

function gearKindTabDefs() {
  const tpl = document.getElementById("gear-kind-tabs");
  if (tpl) {
    return [...tpl.content.querySelectorAll("[data-gear-kind]")].map((btn) => ({
      id: btn.dataset.gearKind || "espresso_machine",
      key: btn.getAttribute("data-i18n") || "",
      fallback: (btn.textContent || "").trim(),
    }));
  }
  return [
    { id: "espresso_machine", key: "gear_machine", fallback: "Espresso" },
    { id: "grinder", key: "gear_grinder", fallback: "Kværn" },
    { id: "brewer", key: "gear_brewer", fallback: "Brygger" },
    { id: "scale_kettle", key: "gear_scale_kettle", fallback: "Vægt & Kedel" },
  ];
}

function gearTabLabel(key, fallback) {
  const localized = key ? t(key) : "";
  if (localized && localized !== key) return localized;
  const map = GEAR_KIND_FALLBACKS[key];
  if (map) return map[activeLang()] || map.da || fallback;
  return fallback || key;
}

function filterGearByKind(items, kind) {
  const slot = gearKindOf({ kind, type: kind });
  const rows = Array.isArray(items) ? items : [];
  if (!GEAR_SLOTS.includes(slot)) return [];
  return rows.filter((item) => gearKindOf(item) === slot);
}

function gearNameOf(item) {
  return String(item?.model_name || item?.name || "").trim();
}

function gearKindLabel(kind) {
  const slot = gearKindOf({ kind });
  const def = gearKindTabDefs().find((row) => row.id === slot);
  const key = def?.key || {
    espresso_machine: "gear_machine",
    machine: "gear_machine",
    grinder: "gear_grinder",
    brewer: "gear_brewer",
    scale_kettle: "gear_scale_kettle",
    other: "gear_other",
  }[kind] || "gear_other";
  return gearTabLabel(key, def?.fallback || t("gear_other"));
}

function gearSpecChips(item) {
  const chips = Array.isArray(item?.highlights) ? item.highlights.filter(Boolean) : [];
  if (chips.length) return chips.slice(0, 6);
  const specs = item?.specs && typeof item.specs === "object" ? item.specs : {};
  return Object.entries(specs).flatMap(([key, val]) => {
    if (val === true) return [key.toLowerCase() === "pid" ? "PID" : key];
    if (!val || val === false) return [];
    const text = String(val).trim();
    return text && !/^n\/?a$/i.test(text) ? [text] : [];
  }).slice(0, 6);
}

function gearImgFallback() {
  return ` onerror="this.onerror=null;this.src='/static/img/gear/placeholder.svg';"`;
}

function gearThumb(item) {
  const src = item?.image_url || GEAR_IMG_FALLBACK;
  const img = photoImg(src, "", "h-full w-full object-contain", gearImgFallback());
  if (img) return img;
  return `<img src="/static/img/gear/placeholder.svg" alt="" class="h-full w-full object-contain" onerror="this.onerror=null;this.src='/static/img/gear/placeholder.svg';">`;
}

function normalizeClientGear(item) {
  const name = gearNameOf(item);
  const kind = gearKindOf(item);
  const chips = gearSpecChips(item);
  return {
    id: item.id || name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || name,
    kind,
    gear_type: { espresso_machine: "machine", grinder: "grinder", brewer: "brewer", scale_kettle: "scale_kettle" }[kind] || "other",
    name,
    model_name: name,
    brand: String(item.brand || "").trim(),
    image_url: item.image_url || "",
    highlights: chips,
    specs: item.specs && typeof item.specs === "object" ? item.specs : {},
    summary: item.summary || "",
    details: item.details || item.specs || {},
  };
}

function gearCard(item) {
  const name = gearNameOf(item);
  if (!name) return "";
  const chips = gearSpecChips(item).map((chip) =>
    `<span class="rounded-full bg-foam px-2 py-0.5 text-[11px] font-semibold">${esc(chip)}</span>`
  ).join("");
  const custom = item.specs?.custom
    ? `<span class="rounded-full bg-foam px-2 py-0.5 text-[11px] font-semibold" data-i18n="gear_custom_badge">${esc(t("gear_custom_badge"))}</span>`
    : "";
  return `<article class="flex gap-3 rounded-2xl bg-white px-3 py-3 shadow-sm ring-1 ring-latte">
    <div class="gear-saved-photo">${gearThumb(item)}</div>
    <div class="min-w-0 flex-1">
      <p class="text-[11px] font-semibold uppercase tracking-wider text-muted">${esc(gearKindLabel(gearKindOf(item)))}</p>
      <h3 class="font-display text-lg font-bold leading-tight">${esc(name)}</h3>
      ${item.brand ? `<p class="text-xs text-muted">${esc(item.brand)}</p>` : ""}
      ${chips || custom ? `<div class="mt-2 flex flex-wrap gap-1">${chips}${custom}</div>` : ""}
      <div class="mt-2 flex gap-3">
        <button type="button" data-gear-edit="${esc(item.id)}" class="text-xs font-semibold text-terracotta" data-i18n="gear_edit">${esc(t("gear_edit"))}</button>
        <button type="button" data-gear-remove="${esc(item.id)}" class="text-xs font-semibold text-muted" data-i18n="gear_remove">${esc(t("gear_remove"))}</button>
      </div>
    </div>
  </article>`;
}

function gearPickerCard(item) {
  const name = gearNameOf(item);
  if (!name) return "";
  const chips = gearSpecChips(item).slice(0, 4).map((chip) =>
    `<span class="rounded-full bg-foam px-1.5 py-0.5 text-[10px] font-semibold">${esc(chip)}</span>`
  ).join("");
  return `<button type="button" data-gear-pick="${esc(item.id)}" class="rounded-2xl bg-white p-2.5 text-left shadow-sm ring-1 ring-latte">
    <div class="gear-picker-photo">${gearThumb(item)}</div>
    <p class="mt-2 text-[10px] font-semibold uppercase tracking-wider text-muted">${esc(gearKindLabel(gearKindOf(item)))}</p>
    <h3 class="font-display text-sm font-bold leading-tight">${esc(name)}</h3>
    ${item.brand ? `<p class="text-[11px] text-muted">${esc(item.brand)}</p>` : ""}
    ${chips ? `<div class="mt-1.5 flex flex-wrap gap-1">${chips}</div>` : ""}
    <span class="mt-2 block text-xs font-semibold text-terracotta" data-i18n="gear_select">${esc(t("gear_select"))}</span>
  </button>`;
}

function gearPickerModal() {
  if (!state.gearPickerOpen) return "";
  const cards = filterGearByKind(state.gearCandidates, state.gearKind).map(gearPickerCard).join("");
  return `<div id="gear-picker" data-close-gear-picker class="fixed inset-0 z-50 flex items-end justify-center bg-espresso/50 px-0 sm:items-center sm:px-4">
    <article class="relative mb-0 max-h-[90dvh] w-full max-w-lg overflow-y-auto rounded-t-3xl bg-cream shadow-2xl sm:mb-0 sm:rounded-3xl" data-gear-picker-sheet>
      <div class="modal-close-bar">
        <button type="button" data-close-gear-picker class="grid h-10 w-10 place-items-center rounded-full bg-cream/95 text-lg font-semibold shadow" data-i18n-aria="close_detail" aria-label="${esc(t("close_detail"))}">✕</button>
      </div>
      <div class="space-y-3 p-4 pb-8 pt-12">
        <div>
          <h2 class="font-display text-xl font-bold" data-i18n="gear_picker_title">${esc(t("gear_picker_title"))}</h2>
          <p class="mt-1 text-sm text-muted" data-i18n="gear_picker_sub">${esc(t("gear_picker_sub"))}</p>
        </div>
        <div class="grid grid-cols-2 gap-2">${cards}</div>
        <button type="button" data-open-gear-custom class="flex min-h-12 w-full items-center justify-center rounded-xl bg-foam text-sm font-semibold ring-1 ring-latte" data-i18n="gear_custom_photo">${esc(t("gear_custom_photo"))}</button>
      </div>
    </article>
  </div>`;
}

function gearCustomModal() {
  if (!state.gearCustomOpen) return "";
  const preview = state.gearCustomImage
    ? `<div class="gear-picker-photo mx-auto w-40">${photoImg(state.gearCustomImage, "", "h-full w-full object-contain", gearImgFallback())}</div>`
    : `<p class="text-center text-sm text-muted" data-i18n="gear_no_image">${esc(t("gear_no_image"))}</p>`;
  return `<div id="gear-custom" data-close-gear-custom class="fixed inset-0 z-[60] flex items-end justify-center bg-espresso/50 px-4 sm:items-center">
    <article class="mb-20 w-full max-w-sm overflow-hidden rounded-3xl bg-cream shadow-2xl sm:mb-0" data-gear-custom-sheet>
      <div class="space-y-3 p-5">
        <h2 class="font-display text-xl font-bold" data-i18n="gear_custom_title">${esc(t("gear_custom_title"))}</h2>
        ${preview}
        <input id="gear-custom-name" value="${esc(state.gearCustomName)}" class="min-h-12 w-full rounded-xl border border-latte bg-white px-3 text-sm" data-i18n-placeholder="gear_custom_name_ph" placeholder="${esc(t("gear_custom_name_ph"))}">
        <input id="gear-custom-brand" value="${esc(state.gearCustomBrand)}" class="min-h-12 w-full rounded-xl border border-latte bg-white px-3 text-sm" data-i18n-placeholder="gear_custom_brand_ph" placeholder="${esc(t("gear_custom_brand_ph"))}">
        <input id="gear-photo-input" type="file" accept="image/*" class="sr-only">
        <button type="button" id="gear-photo-pick" class="flex min-h-12 w-full items-center justify-center rounded-xl bg-foam text-sm font-semibold ring-1 ring-latte" data-i18n="gear_custom_photo">${esc(t("gear_custom_photo"))}</button>
        <button type="button" id="gear-custom-save" class="min-h-12 w-full rounded-xl bg-terracotta text-sm font-semibold text-cream" data-i18n="gear_custom_save">${esc(t("gear_custom_save"))}</button>
        <button type="button" data-close-gear-custom class="min-h-11 w-full text-sm font-semibold text-muted" data-i18n="close_detail">${esc(t("close_detail"))}</button>
      </div>
    </article>
  </div>`;
}

function gearSetup() {
  const gear = userGear();
  const kindBtns = gearKindTabDefs().map(({ id, key, fallback }) => {
    const on = state.gearKind === id;
    const label = gearTabLabel(key, fallback);
    const i18n = key && t(key) !== key ? ` data-i18n="${key}"` : "";
    return `<button type="button" data-gear-kind="${id}" class="gear-tab${on ? " is-active" : ""}"${i18n}>${esc(label)}</button>`;
  }).join("");
  const saved = gear.gear_specs.map((item) => gearCard(item)).join("");
  return `<section class="space-y-3 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-latte">
    <div>
      <h2 class="font-display text-xl font-bold" data-i18n="gear_setup">${esc(t("gear_setup"))}</h2>
      <p class="mt-1 text-sm text-muted" data-i18n="gear_setup_sub">${esc(t("gear_setup_sub"))}</p>
    </div>
    <div class="gear-kind-tabs">${kindBtns}</div>
    <div class="flex gap-2">
      <input id="gear-query" value="${esc(state.gearQuery)}" class="min-h-12 flex-1 rounded-xl border border-latte bg-cream px-3 text-sm" data-i18n-placeholder="gear_search_ph" placeholder="${esc(t("gear_search_ph"))}">
      <button type="button" id="gear-lookup" class="min-h-12 shrink-0 rounded-xl bg-terracotta px-3 text-sm font-semibold text-cream" data-i18n="gear_lookup">${esc(t("gear_lookup"))}</button>
    </div>
    <button type="button" data-open-gear-custom class="flex min-h-11 w-full items-center justify-center rounded-xl bg-foam text-sm font-semibold ring-1 ring-latte" data-i18n="gear_custom_photo">${esc(t("gear_custom_photo"))}</button>
    ${saved || `<p class="text-sm text-muted" data-i18n="gear_empty">${esc(t("gear_empty"))}</p>`}
  </section>`;
}

function journalBeanGroups(rows) {
  const groups = [];
  const index = new Map();
  for (const row of rows || []) {
    const id = Number(row.bean_id || 0);
    const key = id ? String(id) : `${row.bean_name || ""}|${row.roaster || ""}`;
    if (!index.has(key)) {
      const group = {
        key,
        bean_id: id,
        bean_name: row.bean_name || "",
        roaster: row.roaster || "",
        image_url: row.bean_image_url || row.image_url || "",
        entries: [],
      };
      index.set(key, group);
      groups.push(group);
    }
    const group = index.get(key);
    group.entries.push(row);
    if (!group.image_url) group.image_url = row.bean_image_url || row.image_url || "";
  }
  for (const group of groups) {
    const scores = group.entries.map((row) => Number(row.rating || 0)).filter((n) => n > 0);
    group.avg_rating = scores.length ? scores.reduce((sum, n) => sum + n, 0) / scores.length : 0;
    group.count = group.entries.length;
  }
  return groups;
}

function journalBeanCard(group) {
  const open = !!state.journalOpenBeans[group.key];
  const img = photoImg(group.image_url, "", "h-14 w-14 shrink-0 rounded-xl object-cover bg-foam") || bagThumb();
  const entries = open
    ? `<div class="space-y-2 border-t border-latte bg-cream px-3 py-3">${group.entries.map((row) => recipeCard(row)).join("")}</div>`
    : "";
  return `<article class="overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-latte">
    <button type="button" data-toggle-journal-bean="${esc(group.key)}" class="flex w-full items-center gap-3 px-3 py-3 text-left" aria-expanded="${open ? "true" : "false"}">
      ${img}
      <div class="min-w-0 flex-1">
        <h3 class="font-display truncate text-base font-bold leading-tight">${esc(group.bean_name)}</h3>
        ${group.roaster ? `<p class="truncate text-xs text-muted">${esc(group.roaster)}</p>` : ""}
        <p class="mt-1 text-xs font-semibold text-espresso">⭐ ${esc(group.avg_rating.toFixed(1))} · ${esc(group.count === 1 ? t("journal_tasting") : t("journal_tastings", { n: group.count }))}</p>
      </div>
      <span class="journal-bean-chevron${open ? " is-open" : ""}" aria-hidden="true">▾</span>
    </button>
    ${entries}
  </article>`;
}

function journalCard(row, compact = false) {
  const date = (row.created_at || "").slice(0, 10);
  const method = localizeBrewMethod(row.brew_method);
  const rating = Number(row.rating || 0).toFixed(1);
  if (compact) {
    const sub = [row.roaster, method].filter(Boolean).join(" · ");
    return `<article class="journal-row">
      <time class="journal-row-date" datetime="${esc(date)}">${esc(date)}</time>
      <div class="journal-row-body">
        <h3>${esc(row.bean_name || "")}</h3>
        ${sub ? `<p>${esc(sub)}</p>` : ""}
      </div>
      <span class="journal-row-score">⭐ ${esc(rating)}</span>
    </article>`;
  }
  return recipeCard(row);
}

function journalFeed() {
  const groups = journalBeanGroups(state.journal);
  const preview = groups.slice(0, 4);
  return `<section class="journal-feed">
    <div class="journal-feed-head">
      <div class="min-w-0">
        <h2 class="font-display text-lg font-bold" data-i18n="journal_title">${esc(t("journal_title"))}</h2>
        <p class="text-xs text-muted" data-i18n="journal_grouped">${esc(t("journal_grouped"))}</p>
      </div>
      <button type="button" data-open-journal class="journal-open-btn" data-i18n="journal_view_all">${esc(t("journal_view_all"))}</button>
    </div>
    ${preview.length ? preview.map((group) => journalBeanCard(group)).join("") : `<p class="rounded-2xl bg-white px-3 py-3 text-sm text-muted shadow-sm ring-1 ring-latte" data-i18n="journal_empty">${esc(t("journal_empty"))}</p>`}
  </section>`;
}

function journalModal() {
  if (!state.journalOpen) return "";
  const groups = journalBeanGroups(state.journal);
  const list = groups.length
    ? groups.map((group) => journalBeanCard(group)).join("")
    : `<p class="rounded-2xl bg-white px-3 py-3 text-sm text-muted shadow-sm ring-1 ring-latte" data-i18n="journal_empty">${esc(t("journal_empty"))}</p>`;
  return `<div id="journal-modal" data-close-journal class="journal-modal">
    <article class="journal-modal-sheet" data-journal-sheet>
      <div class="flex items-center justify-between gap-3 px-4 pb-2 pt-4">
        <div class="min-w-0">
          <h2 class="font-display text-xl font-bold" data-i18n="journal_title">${esc(t("journal_title"))}</h2>
          <p class="text-xs text-muted" data-i18n="journal_grouped">${esc(t("journal_grouped"))}</p>
        </div>
        <button type="button" data-close-journal class="grid h-10 w-10 place-items-center rounded-full bg-white text-lg font-semibold shadow-sm ring-1 ring-latte" data-i18n-aria="close_detail" aria-label="${esc(t("close_detail"))}">✕</button>
      </div>
      <div class="journal-modal-list">${list}</div>
    </article>
  </div>`;
}

function langToggle() {
  return i18nManager.renderLanguageSwitcher();
}

function profileView() {
  return `<section class="space-y-4 px-4 pb-28 pt-5">
    <div class="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-latte">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-xs uppercase tracking-wider text-muted" data-i18n="signed_in_as">${t("signed_in_as")}</p>
          <p class="font-display text-xl font-bold">${esc(state.user?.username || state.user?.email)}</p>
          <p class="text-sm text-muted">${esc(state.user?.email)} · ${esc(state.user?.auth_provider)}</p>
          ${isAdmin() ? `<p class="mt-2 inline-flex rounded-full bg-[#f4ebd9] px-2.5 py-1 text-xs font-semibold">${esc(t("admin_badge"))}</p>` : ""}
        </div>
        ${langToggle()}
      </div>
      <button type="button" data-open-journal class="mt-3 flex min-h-11 w-full items-center justify-center rounded-xl bg-foam text-sm font-semibold ring-1 ring-latte" data-i18n="journal_title">${esc(t("journal_title"))}</button>
    </div>
    ${journalFeed()}
    ${gearSetup()}
    ${supportButton()}
    <button id="logout" class="min-h-12 w-full rounded-xl bg-espresso font-semibold text-cream" data-i18n="logout">${t("logout")}</button>
  </section>`;
}

function tabbar() {
  const tabs = [
    ["explore", "☕", "tab_explore"],
    ["scan", "📸", "tab_scan"],
    ["profile", "👤", "tab_profile"],
  ];
  return `<nav class="tabbar fixed inset-x-0 bottom-0 z-30 mx-auto max-w-lg border-t border-latte bg-cream/95 backdrop-blur">
    <div class="grid grid-cols-3">
      ${tabs.map(([id, icon, key]) => {
        const active = state.tab === id;
        const scan = id === "scan";
        return `
        <button data-tab="${id}" type="button" class="flex min-h-14 flex-col items-center justify-center text-[11px] font-semibold ${active || scan ? "text-terracotta" : "text-muted"}">
          ${scan
            ? `<span class="tabbar-scan-icon flex h-11 w-11 items-center justify-center rounded-full bg-terracotta text-lg text-cream ring-4 ring-cream">${icon}</span>`
            : `<span class="text-lg">${icon}</span>`}
          <span data-i18n="${key}">${esc(t(key))}</span>
        </button>`;
      }).join("")}
    </div>
  </nav>`;
}

function coffeeIcon(beanId) {
  const idAttr = beanId != null ? ` data-open-bean="${beanId}"` : "";
  return L.divIcon({
    className: "coffee-pin",
    html: `<span aria-hidden="true"${idAttr}>☕</span>`,
    iconSize: [32, 32],
    iconAnchor: [16, 28],
    popupAnchor: [0, -24],
  });
}

function addOsm(map) {
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 18,
  }).addTo(map);
}

function destroyMaps() {
  if (originMap) {
    originMap.remove();
    originMap = null;
  }
  if (worldMap) {
    worldMap.remove();
    worldMap = null;
  }
}

function refreshMap(map, place) {
  if (!map) return;
  const run = () => {
    map.invalidateSize();
    if (place) place();
  };
  requestAnimationFrame(() => {
    run();
    setTimeout(run, 200);
  });
}

function drawMaps() {
  if (!window.L) return;
  destroyMaps();
  const origin = $("#origin-map");
  if (origin) {
    const lat = Number(origin.dataset.lat);
    const lng = Number(origin.dataset.lng);
    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      originMap = L.map(origin, {
        scrollWheelZoom: false,
        zoomControl: true,
      }).setView([lat, lng], 7);
      addOsm(originMap);
      L.marker([lat, lng], { icon: coffeeIcon() })
        .addTo(originMap)
        .bindPopup(origin.dataset.label || t("map_origin"));
      refreshMap(originMap, () => originMap.setView([lat, lng], 7));
    }
  }
  const world = $("#world-map");
  if (world) {
    worldMap = L.map(world, { scrollWheelZoom: false, worldCopyJump: true }).setView([20, 0], 2);
    addOsm(worldMap);
    const points = [];
    visibleBeans().forEach((bean) => {
      const lat = Number(bean.latitude);
      const lng = Number(bean.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      const marker = L.marker([lat, lng], { icon: coffeeIcon() }).addTo(worldMap);
      marker.bindPopup(coffeeMiniCard(bean), {
        className: "bn-mini-popup",
        maxWidth: 280,
        minWidth: 220,
        closeButton: true,
        autoPan: true,
      });
      marker.on("popupopen", () => {
        const btn = marker.getPopup()?.getElement()?.querySelector("[data-open-bean]");
        btn?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          marker.closePopup();
          openBean(Number(bean.id));
        });
      });
      points.push([lat, lng]);
    });
    const placeWorld = () => {
      if (points.length === 1) worldMap.setView(points[0], 5);
      else if (points.length > 1) worldMap.fitBounds(points, { padding: [36, 36], maxZoom: 4 });
      else worldMap.setView([20, 0], 2);
    };
    refreshMap(worldMap, placeWorld);
  }
}

function savedPromptModal() {
  const prompt = state.savedPrompt;
  if (!prompt) return "";
  return `<div id="saved-prompt" class="fixed inset-0 z-50 flex items-end justify-center bg-espresso/50 px-4 sm:items-center">
    <article class="mb-20 w-full max-w-sm rounded-3xl bg-cream p-5 shadow-2xl sm:mb-0">
      <h2 class="font-display text-2xl font-bold">${esc(t("saved_prompt_title"))}</h2>
      <p class="mt-2 text-sm text-muted">${esc(t("saved_prompt_sub"))}</p>
      <div class="mt-5 grid gap-2">
        <button id="saved-rate" class="min-h-12 w-full rounded-xl bg-terracotta font-semibold text-cream">${esc(t("saved_prompt_rate"))}</button>
        <button id="saved-explore" class="min-h-12 w-full rounded-xl bg-white font-semibold text-espresso ring-1 ring-latte">${esc(t("saved_prompt_explore"))}</button>
      </div>
    </article>
  </div>`;
}

async function resetExplore() {
  state.tab = "explore";
  state.search = "";
  state.exploreMode = "cards";
  state.suitabilityFilter = "";
  state.beanFilter = "all";
  state.selectedId = null;
  state.profile = null;
  state.editBean = false;
  state.rateOpen = false;
  state.savedPrompt = null;
  state.journalOpen = false;
  await loadBeans();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function closeBean() {
  if (!state.profile && state.selectedId == null) return;
  state.selectedId = null;
  state.profile = null;
  state.editBean = false;
  state.rateOpen = false;
  render();
}

function beanPayload(source) {
  return {
    name: source.name,
    roaster: source.roaster,
    origin: source.origin,
    process: source.process,
    roast_level: source.roast_level,
    roaster_notes: source.roaster_notes,
    flavor_tags: source.flavor_tags || {},
    suitable_for: source.suitable_for || [],
    story: source.story || {},
    image_url: source.image_url,
    roaster_url: source.roaster_url || "",
    recommended_method: source.recommended_method || brewSource(source).method || "",
    grind_size: source.grind_size || brewSource(source).grind || "",
    water_temp: source.water_temp || brewSource(source).temp || "",
    brew_ratio: source.brew_ratio || brewSource(source).ratio || "",
    brew_recommendation: source.brew_recommendation || {},
    roast_date: source.roast_date || "",
    altitude: source.altitude || "",
    varietal: source.varietal || "",
    latitude: source.latitude,
    longitude: source.longitude,
    region_full: source.region_full || "",
    acidity_score: source.roaster_acidity ?? source.acidity_score ?? null,
    body_score: source.roaster_body ?? source.body_score ?? null,
    roast_level_score: source.roaster_roast_level ?? source.roast_level_score ?? null,
    roaster_acidity: source.roaster_acidity ?? source.acidity_score ?? null,
    roaster_body: source.roaster_body ?? source.body_score ?? null,
    roaster_roast_level: source.roaster_roast_level ?? source.roast_level_score ?? null,
    skip_fuzzy: false,
  };
}

function render() {
  destroyMaps();
  const root = $("#app");
  if (!state.config) {
    root.innerHTML = `<p class="p-8 text-center text-muted">BeanNote…</p>`;
    return;
  }
  document.documentElement.lang = state.config.lang || i18nManager.FALLBACK_LANG;
  document.body.classList.toggle("modal-open", !!(state.user && ((state.tab === "explore" && state.profile?.bean) || state.savedPrompt || state.supportOpen || state.gearPickerOpen || state.gearCustomOpen || state.journalOpen)));
  if (!state.user) {
    root.innerHTML = authView();
    bindAuth();
    bindLanguageButtons();
    return;
  }
  const body = { explore: exploreView, scan: scanView, profile: profileView }[state.tab]() || exploreView();
  root.innerHTML = `${header()}${body}${tabbar()}${savedPromptModal()}${supportModal()}${gearPickerModal()}${gearCustomModal()}${journalModal()}${state.toast ? `<div class="bn-toast fixed inset-x-4 top-4 rounded-xl bg-espresso px-4 py-3 text-sm text-cream shadow-lg">${esc(state.toast)}</div>` : ""}${coffeeLoaderOverlay()}`;
  bindApp();
  drawMaps();
}

function bindAuth() {
  document.querySelectorAll("[data-oauth]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.preventDefault();
      const provider = btn.dataset.oauth;
      if (state.config?.local_dev || ["local", "dev"].includes(state.config?.environment)) {
        try {
          const result = await api(`/api/auth/${provider}/dev`, { method: "POST", body: "{}" });
          setAuth(result);
          await loadBeans();
          await loadJournal();
          render();
        } catch (err) {
          toast(t(err.detail || "oauth_unavailable"));
        }
        return;
      }
      if (state.config?.providers?.[provider]) {
        window.location.href = `/api/auth/${provider}`;
        return;
      }
      toast(t("oauth_unavailable"));
    });
  });
  $("#toggle-auth")?.addEventListener("click", () => {
    state.authMode = state.authMode === "login" ? "register" : "login";
    render();
  });
  $("#auth-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      email: String(form.get("email") || ""),
      password: String(form.get("password") || ""),
      username: String(form.get("username") || ""),
    };
    try {
      const path = state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
      const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
      setAuth(result);
      await loadBeans();
      await loadJournal();
      render();
    } catch (err) {
      toast(t(err.detail || "invalid_credentials"));
    }
  });
}

function readScanForm(base = state.scan) {
  const form = $("#scan-edit");
  if (!form || !base) return base;
  const data = new FormData(form);
  return {
    ...base,
    name: data.get("name"),
    roaster: data.get("roaster"),
    roaster_url: data.get("roaster_url"),
    origin: data.get("origin"),
    region_full: data.get("region_full"),
    roast_date: data.get("roast_date"),
    altitude: data.get("altitude"),
    varietal: data.get("varietal"),
    process: data.get("process"),
    roast_level: data.get("roast_level"),
    roaster_notes: data.get("roaster_notes"),
    story: i18nManager.setLocalized(base.story, data.get("story")),
  };
}

function triggerScanPicker() {
  const input = document.getElementById("scan-input");
  if (!input || state.busy) return;
  input.value = "";
  input.click();
}

function bindScanInput() {
  const input = document.getElementById("scan-input");
  if (!input || input.dataset.bound === "1") return;
  input.dataset.bound = "1";
  input.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    input.value = "";
    if (file) uploadScan(file);
  });
}

async function uploadScan(file) {
  if (!file) {
    toast(t("scan_no_file"));
    return;
  }
  state.tab = "scan";
  startBusy();
  render();
  try {
    const body = new FormData();
    const lang = activeLang();
    body.append("file", file, file.name || "scan.jpg");
    body.append("lang", lang);
    const queued = await api(`/api/scan?lang=${encodeURIComponent(lang)}`, { method: "POST", body });
    const scan = await waitForJob(queued);
    stopBusy();
    if (scan.scan_fallback === "gemini_quota") toast(t("ocr_quota"));
    const existing = existingScanMatch(scan);
    if (existing?.id) {
      state.scan = null;
      state.editScan = false;
      await openBean(Number(existing.id));
      return;
    }
    state.scan = scan;
    state.tab = "scan";
    render();
  } catch (err) {
    stopBusy();
    const detail = typeof err.detail === "string" ? err.detail : "";
    const offline = !detail && /failed to fetch|networkerror|load failed|err_connection/i.test(String(err.message || err));
    toast(t(offline ? "scan_offline" : (detail || "ocr_fail")));
    render();
  } finally {
    if (state.busy) {
      stopBusy();
      render();
    }
  }
}

function bindApp() {
  document.querySelectorAll("[data-tab]").forEach((btn) => btn.addEventListener("click", async () => {
    if (btn.dataset.tab === "explore") {
      await resetExplore();
      return;
    }
    if (btn.dataset.tab === "scan") {
      state.journalOpen = false;
      if (state.busy) return;
      if (state.scan) {
        state.tab = "scan";
        render();
        return;
      }
      triggerScanPicker();
      return;
    }
    state.journalOpen = false;
    state.tab = btn.dataset.tab;
    if (state.tab === "rate") {
      state.tab = "explore";
    }
    if (state.tab === "profile") await loadJournal();
    render();
  }));
  document.querySelectorAll("[data-suitable]").forEach((btn) => btn.addEventListener("click", () => {
    state.suitabilityFilter = btn.dataset.suitable || "";
    render();
  }));
  $("#saved-rate")?.addEventListener("click", async () => {
    const id = Number(state.savedPrompt?.id);
    state.savedPrompt = null;
    if (id) await openBean(id, "rate");
    else render();
  });
  $("#saved-explore")?.addEventListener("click", async () => {
    await resetExplore();
  });
  const runSearch = async () => {
    await loadBeans();
    const box = $("#search");
    const pos = box ? box.selectionStart : null;
    const value = box ? box.value : state.search;
    state.search = value;
    render();
    const next = $("#search");
    if (next && document.activeElement === document.body) {
      next.focus();
      if (pos != null) next.setSelectionRange(pos, pos);
    } else if (next && pos != null) {
      next.focus();
      next.setSelectionRange(pos, pos);
    }
  };
  $("#sortBeansSelect")?.addEventListener("change", (event) => {
    state.beanSort = event.target.value || "newest";
    render();
  });
  $("#search")?.addEventListener("input", (event) => {
    state.search = event.target.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(runSearch, 280);
  });
  $("#search")?.addEventListener("change", async (event) => {
    state.search = event.target.value;
    clearTimeout(state.searchTimer);
    await runSearch();
  });
  document.querySelectorAll("[data-filter]").forEach((btn) => btn.addEventListener("click", async () => {
    state.beanFilter = btn.dataset.filter;
    state.selectedId = null;
    state.profile = null;
    await loadBeans();
    render();
  }));
  document.querySelectorAll("[data-view]").forEach((btn) => btn.addEventListener("click", () => {
    state.exploreMode = btn.dataset.view;
    render();
  }));
  document.querySelectorAll("[data-fav]").forEach((btn) => btn.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      const result = await api(`/api/beans/${btn.dataset.fav}/favorite`, { method: "POST", body: "{}" });
      const id = Number(result.bean_id);
      state.beans = state.beans.map((bean) => bean.id === id ? { ...bean, is_favorite: result.is_favorite } : bean);
      if (state.profile?.bean?.id === id) state.profile.bean.is_favorite = result.is_favorite;
      if (state.beanFilter === "favorites") await loadBeans();
      toast(t(result.is_favorite ? "favorite_saved" : "favorite_removed"));
      render();
    } catch (err) {
      toast(t(err.detail || "auth_required"));
    }
  }));
  document.querySelectorAll("[data-open-bean]").forEach((btn) => btn.addEventListener("click", () => openBean(Number(btn.dataset.openBean))));
  document.querySelectorAll("[data-rate-bean]").forEach((btn) => btn.addEventListener("click", () => openBean(Number(btn.dataset.rateBean), "rate")));
  document.querySelectorAll("[data-open-archive]").forEach((btn) => btn.addEventListener("click", () => openBean(Number(btn.dataset.openArchive), "rate")));
  $("#open-rate-form")?.addEventListener("click", () => {
    state.rateOpen = true;
    render();
    requestAnimationFrame(() => $("#rate-form")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  });
  document.querySelectorAll("[data-enrich-bean]").forEach((btn) => btn.addEventListener("click", async () => {
    const id = state.profile?.bean?.id;
    if (!id) return;
    startBusy();
    render();
    try {
      const queued = await api(`/api/beans/${id}/enrich?lang=${encodeURIComponent(activeLang())}`, { method: "POST", body: "{}" });
      const result = await waitForJob(queued);
      if (result.profile) state.profile = result.profile;
      else if (result.bean) state.profile = { ...(state.profile || {}), bean: result.bean };
      if (result.bean) {
        state.beans = state.beans.map((bean) => bean.id === result.bean.id ? { ...bean, ...result.bean } : bean);
      }
      stopBusy();
      toast(t("enrich_ok"));
      render();
    } catch (err) {
      stopBusy();
      toast(t(err.detail || "enrich_fail"));
      render();
    }
  }));
  $("[data-modal-sheet]")?.addEventListener("click", (event) => event.stopPropagation());
  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.currentTarget === event.target) closeBean();
    });
  });
  document.querySelectorAll("[data-open-support]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.supportOpen = true;
      render();
    });
  });
  $("[data-support-sheet]")?.addEventListener("click", (event) => event.stopPropagation());
  document.querySelectorAll("[data-close-support]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.currentTarget === event.target || el.tagName === "BUTTON") {
        state.supportOpen = false;
        render();
      }
    });
  });
  $("#pick-scan")?.addEventListener("click", () => triggerScanPicker());
  $("#undo-scan")?.addEventListener("click", () => {
    resetScanPreview();
    triggerScanPicker();
  });
  $("#toggle-edit")?.addEventListener("click", () => {
    if (!isAdmin()) return;
    state.editScan = !state.editScan;
    render();
  });
  $("#toggle-bean-edit")?.addEventListener("click", () => {
    if (!isAdmin()) return;
    state.editBean = !state.editBean;
    render();
  });
  $("#save-masterdata")?.addEventListener("click", async () => {
    if (!isAdmin() || !state.profile?.bean?.id) return;
    const source = readScanForm(state.profile.bean);
    try {
      const result = await api(`/api/beans/${state.profile.bean.id}`, {
        method: "PUT",
        body: JSON.stringify(beanPayload(source)),
      });
      if (result.bean) {
        state.profile.bean = result.bean;
        state.beans = state.beans.map((bean) => bean.id === result.bean.id ? { ...bean, ...result.bean } : bean);
      }
      state.editBean = false;
      toast(t("saved_toast"));
      render();
    } catch (err) {
      toast(t(err.detail || "forbidden"));
    }
  });
  document.querySelectorAll("[data-cover-url]").forEach((btn) => {
    btn.addEventListener("click", () => selectScanCover(btn.dataset.coverUrl));
  });
  $("#use-own-photo")?.addEventListener("click", () => {
    selectScanCover(state.scan?.snapshot_url || state.scan?.preview || "");
  });
  $("#approve-bean")?.addEventListener("click", async () => {
    const scan = isAdmin() ? readScanForm() : state.scan;
    try {
      const result = await api("/api/beans", {
        method: "POST",
        body: JSON.stringify(beanPayload(scan)),
      });
      if (result.status === "fuzzy") {
        state.scan = { ...scan, similar: result.similar };
        toast(t("duplicate_warning"));
        render();
        return;
      }
      const bean = result.bean;
      state.scan = null;
      await loadBeans();
      if (bean?.id) {
        state.savedPrompt = { id: bean.id, name: bean.name };
        state.tab = "scan";
        render();
        return;
      }
      render();
    } catch (err) {
      toast(t(err.detail || "required"));
    }
  });
  document.querySelectorAll("[data-star]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      const n = Number(btn.dataset.star);
      const rect = btn.getBoundingClientRect();
      const half = (event.clientX - rect.left) < rect.width / 2;
      state.rate.rating = half ? Math.max(0.5, n - 0.5) : n;
      syncRateUi();
    });
  });
  document.querySelectorAll("[data-sense]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.sense;
      if (!key) return;
      state.rate[key] = Number(btn.dataset.value);
      syncRateUi();
    });
  });
  $("#brew")?.addEventListener("change", (event) => {
    state.rate.brew_method = event.target.value;
    state.rate.brewMethodManual = true;
  });
  $("#unlock-brew-method")?.addEventListener("click", () => {
    state.rate.brewMethodManual = true;
    render();
    $("#brew")?.focus();
  });
  $("#rate_espresso_machine")?.addEventListener("change", (event) => {
    state.rate.espresso_machine = event.target.value;
    state.rate.brewMethodManual = false;
    const implied = impliedBrewMethod();
    if (implied) state.rate.brew_method = implied;
    render();
  });
  $("#rate_grinder")?.addEventListener("change", (event) => { state.rate.grinder = event.target.value; });
  $("#notes")?.addEventListener("input", (event) => { state.rate.notes = event.target.value; });
  $("#grind_setting")?.addEventListener("input", (event) => { state.rate.grind_setting = event.target.value; });
  $("#brew_time")?.addEventListener("input", (event) => { state.rate.brew_time = event.target.value; });
  $("#brew_time")?.addEventListener("blur", (event) => {
    const next = normalizeBrewTime(event.target.value);
    state.rate.brew_time = next;
    event.target.value = next;
  });
  ["coffee_grams", "water_grams"].forEach((key) => {
    const el = $(`#${key}`);
    if (!el) return;
    el.addEventListener("input", (event) => {
      const next = sanitizeGramsTyping(event.target.value);
      if (next !== event.target.value) event.target.value = next;
      state.rate[key] = event.target.value;
    });
    el.addEventListener("blur", (event) => {
      const formatted = formatGramsInput(event.target.value);
      event.target.value = formatted;
      state.rate[key] = formatted;
    });
    el.addEventListener("paste", (event) => {
      const text = event.clipboardData?.getData("text") || "";
      const formatted = formatGramsInput(text);
      if (!formatted && !sanitizeGramsTyping(text)) return;
      event.preventDefault();
      el.value = formatted || sanitizeGramsTyping(text);
      state.rate[key] = el.value;
    });
  });
  $("#save-rating")?.addEventListener("click", async () => {
    if (!state.selectedId) return toast(t("select_bean"));
    try {
      const coffee = parseGrams($("#coffee_grams")?.value ?? state.rate.coffee_grams);
      const water = parseGrams($("#water_grams")?.value ?? state.rate.water_grams);
      const { brewMethodManual, ...ratePayload } = state.rate;
      const result = await api("/api/ratings", {
        method: "POST",
        body: JSON.stringify({
          bean_id: Number(state.selectedId),
          ...ratePayload,
          tasting_notes_user: state.rate.notes,
          coffee_grams: coffee,
          water_grams: water,
          brew_time: normalizeBrewTime($("#brew_time")?.value ?? state.rate.brew_time),
          brew_method: $("#brew")?.value ?? state.rate.brew_method,
          espresso_machine: $("#rate_espresso_machine")?.value ?? state.rate.espresso_machine,
          grinder: $("#rate_grinder")?.value ?? state.rate.grinder,
        }),
      });
      state.profile = result.profile;
      state.rateOpen = false;
      toast(t("saved_toast"));
      loadJournal();
      render();
    } catch (err) {
      toast(t(err.detail || "required"));
    }
  });
  document.querySelectorAll("[data-recipe-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sheet = $("[data-modal-sheet]");
      const top = sheet ? sheet.scrollTop : 0;
      state.recipeTab = btn.dataset.recipeTab === "community" ? "community" : "mine";
      render();
      const next = $("[data-modal-sheet]");
      if (next) next.scrollTop = top;
    });
  });
  document.querySelectorAll("[data-gear-kind]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.gearKind = btn.dataset.gearKind || "espresso_machine";
      state.gearCandidates = filterGearByKind(state.gearCandidates, state.gearKind);
      if (!state.gearCandidates.length) state.gearPickerOpen = false;
      render();
    });
  });
  $("#gear-query")?.addEventListener("input", (event) => {
    state.gearQuery = event.target.value;
  });
  $("#gear-query")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("#gear-lookup")?.click();
    }
  });
  $("#gear-lookup")?.addEventListener("click", async () => {
    const query = (state.gearQuery || $("#gear-query")?.value || "").trim();
    state.gearQuery = query;
    if (query.length < 2) return toast(t("gear_query_required"));
    startBusy();
    render();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12000);
    try {
      const result = await api("/api/gear/lookup", {
        method: "POST",
        body: JSON.stringify({ query, kind: state.gearKind, lang: activeLang() }),
        signal: ctrl.signal,
      });
      const raw = Array.isArray(result.gear_candidates) && result.gear_candidates.length
        ? result.gear_candidates
        : (result.specs ? [result.specs] : []);
      const hits = filterGearByKind(raw, state.gearKind);
      state.gearCandidates = hits;
      state.gearHit = hits[0] || null;
      state.gearPickerOpen = hits.length > 0;
      stopBusy();
      if (!hits.length) toast(t("gear_lookup_fail"));
      render();
    } catch (err) {
      stopBusy();
      state.gearHit = null;
      state.gearCandidates = [];
      state.gearPickerOpen = false;
      toast(t(err.detail || "gear_lookup_fail"));
      render();
    } finally {
      clearTimeout(timer);
    }
  });
  const closeGearPicker = () => {
    state.gearPickerOpen = false;
    render();
  };
  const openGearCustom = (item = null) => {
    state.gearCustomOpen = true;
    state.gearEditId = item?.id || null;
    state.gearCustomName = item ? gearNameOf(item) : state.gearQuery;
    state.gearCustomBrand = item?.brand || "";
    state.gearCustomImage = item?.image_url || "";
    render();
  };
  document.querySelectorAll("[data-close-gear-picker]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (el.hasAttribute("data-gear-picker-sheet")) return;
      if (event.target === el || el.matches("button")) closeGearPicker();
    });
  });
  $("[data-gear-picker-sheet]")?.addEventListener("click", (event) => event.stopPropagation());
  document.querySelectorAll("[data-open-gear-custom]").forEach((btn) => {
    btn.addEventListener("click", () => openGearCustom());
  });
  document.querySelectorAll("[data-close-gear-custom]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (el.hasAttribute("data-gear-custom-sheet")) return;
      if (event.target === el || el.matches("button")) {
        state.gearCustomOpen = false;
        state.gearEditId = null;
        render();
      }
    });
  });
  $("[data-gear-custom-sheet]")?.addEventListener("click", (event) => event.stopPropagation());
  $("#gear-custom-name")?.addEventListener("input", (event) => {
    state.gearCustomName = event.target.value;
  });
  $("#gear-custom-brand")?.addEventListener("input", (event) => {
    state.gearCustomBrand = event.target.value;
  });
  $("#gear-photo-pick")?.addEventListener("click", () => {
    const input = $("#gear-photo-input");
    if (!input) return;
    input.value = "";
    input.click();
  });
  $("#gear-photo-input")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return toast(t("gear_photo_required"));
    startBusy();
    render();
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await api("/api/gear/photo", { method: "POST", body });
      state.gearCustomImage = result.image_url || "";
      state.gearCustomOpen = true;
      stopBusy();
      render();
    } catch (err) {
      stopBusy();
      toast(t(err.detail || "gear_photo_required"));
      render();
    }
  });
  $("#gear-custom-save")?.addEventListener("click", async () => {
    const name = (state.gearCustomName || "").trim();
    if (!name) return toast(t("gear_name_required"));
    const hit = normalizeClientGear({
      id: state.gearEditId || undefined,
      name,
      model_name: name,
      brand: state.gearCustomBrand,
      kind: state.gearKind,
      image_url: state.gearCustomImage,
      highlights: [],
      specs: { custom: true },
    });
    state.gearCustomOpen = false;
    state.gearPickerOpen = false;
    try {
      await addGearItem(hit);
      state.gearEditId = null;
      state.gearCustomName = "";
      state.gearCustomBrand = "";
      state.gearCustomImage = "";
    } catch (err) {
      state.gearCustomOpen = true;
      toast(t(err.detail || "required"));
      render();
    }
  });
  document.querySelectorAll("[data-gear-pick]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.gearPick;
      const hit = (state.gearCandidates || []).find((item) => item.id === id);
      if (!hit) return;
      state.gearPickerOpen = false;
      try {
        await addGearItem(hit);
      } catch (err) {
        state.gearPickerOpen = true;
        toast(t(err.detail || "required"));
        render();
      }
    });
  });
  document.querySelectorAll("[data-gear-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.gearEdit;
      const item = userGear().gear_specs.find((row) => row.id === id);
      if (item) openGearCustom(item);
    });
  });
  document.querySelectorAll("[data-gear-remove]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.gearRemove;
      const gear = userGear();
      const specs = gear.gear_specs.filter((item) => item.id !== id);
      const machine = specs.find((item) => item.kind === "espresso_machine");
      const mill = specs.find((item) => item.kind === "grinder");
      try {
        await persistGear({
          espresso_machine: machine?.name || "",
          grinder: mill?.name || "",
          brewer_types: gear.brewer_types,
          gear_specs: specs,
        });
      } catch (err) {
        toast(t(err.detail || "required"));
      }
    });
  });
  document.querySelectorAll("[data-open-journal]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.journalOpen = true;
      render();
    });
  });
  document.querySelectorAll("[data-toggle-journal-bean]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const key = btn.dataset.toggleJournalBean;
      if (!key) return;
      const sheet = $(".journal-modal-list");
      const top = sheet ? sheet.scrollTop : window.scrollY;
      state.journalOpenBeans = { ...state.journalOpenBeans, [key]: !state.journalOpenBeans[key] };
      render();
      const next = $(".journal-modal-list");
      if (next) next.scrollTop = top;
      else if (!state.journalOpen) window.scrollTo({ top });
    });
  });
  $("[data-journal-sheet]")?.addEventListener("click", (event) => event.stopPropagation());
  document.querySelectorAll("[data-close-journal]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (el.hasAttribute("data-journal-sheet")) return;
      if (event.target === el || el.matches("button")) {
        state.journalOpen = false;
        render();
      }
    });
  });
  bindLanguageButtons();
  $("#logout")?.addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    localStorage.removeItem(TOKEN_KEY);
    state.user = null;
    state.beans = [];
    render();
  });
}

function bindLanguageButtons() {
  document.querySelectorAll("[data-setlang]").forEach((btn) => {
    btn.addEventListener("click", () => applyLanguage(btn.dataset.setlang));
  });
}

  document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.journalOpen) {
    state.journalOpen = false;
    render();
    return;
  }
  if (state.supportOpen) {
    state.supportOpen = false;
    render();
    return;
  }
  if (state.gearCustomOpen) {
    state.gearCustomOpen = false;
    state.gearEditId = null;
    render();
    return;
  }
  if (state.gearPickerOpen) {
    state.gearPickerOpen = false;
    render();
    return;
  }
  if (state.profile?.bean && state.tab === "explore") closeBean();
});

bindScanInput();
boot();
