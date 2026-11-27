(() => {
  "use strict";

  const TRADING_DAYS_PER_YEAR = 252;
  const MIN_WINDOW_BARS = 5;

  const FIXED_COLORS = {
    QQQ: "#0071E3",
    QLD: "#0891B2",
    TQQQ: "#E11D48",
  };
  const SYNTH_COLOR = "#109868";
  const FALLBACK_PALETTE = [
    "#5856D6",
    "#D97706",
    "#0284C7",
    "#AF52DE",
    "#EA580C",
    "#4F46E5",
    "#0D9488",
    "#65A30D",
  ];

  const MARKET_REGIMES = [
    { label: "2008 次贷危机", start: "2007-10-31", end: "2009-03-09", fill: "rgba(225, 29, 72, 0.055)" },
    { label: "2020 疫情熔断", start: "2020-02-19", end: "2020-03-23", fill: "rgba(217, 119, 6, 0.075)" },
    { label: "2022 加息熊市", start: "2022-01-03", end: "2022-12-28", fill: "rgba(0, 113, 227, 0.055)" },
  ];

  const state = {
    catalog: [],
    catalogMap: new Map(),
    colorByKey: new Map(),
    parseErrors: [],

    selectedKeys: [],
    enableSynth: true,
    synthBase: "QQQ",
    leverage: 1.5,

    preset: "近10年",
    scaleMode: "linear", // "linear" | "log"
    focusCurve: "__ALL__",
    showEndLabels: true,
    showDrawdown: true,
    showRegimes: false,

    masterDates: [],
    masterDateToIdx: new Map(),
    winStartIdx: 0,
    winEndIdx: 0,
    initializedWindow: false,

    hoverIdx: null,
    navHoverZone: null, // "left" | "right" | "window" | "outside" | null
    navDrag: null,
    mainDrag: null,
    measureRange: null, // { startIdx, endIdx }

    sortKey: null,
    sortAsc: false,
  };

  // DOM Elements
  const dom = {
    appShell: document.getElementById("app-shell"),
    sidebarToggleBtn: document.getElementById("sidebar-toggle-btn"),
    selectedCountBadge: document.getElementById("selected-count-badge"),
    addTickerForm: document.getElementById("add-ticker-form"),
    newTickerInput: document.getElementById("new-ticker-input"),
    addTickerBtn: document.getElementById("add-ticker-btn"),
    assetList: document.getElementById("asset-list"),
    refreshBlock: document.getElementById("refresh-block"),
    fullUpdateToggle: document.getElementById("full-update-toggle"),
    refreshMarketBtn: document.getElementById("refresh-market-btn"),
    enableSynthToggle: document.getElementById("enable-synth-toggle"),
    synthControls: document.getElementById("synth-controls"),
    synthBaseSelect: document.getElementById("synth-base-select"),
    leverageInput: document.getElementById("leverage-input"),
    levDecBtn: document.getElementById("lev-dec-btn"),
    levIncBtn: document.getElementById("lev-inc-btn"),
    parseWarning: document.getElementById("parse-warning"),
    statusText: document.getElementById("status-text"),

    presetBar: document.getElementById("preset-bar"),
    startDateInput: document.getElementById("start-date-input"),
    endDateInput: document.getElementById("end-date-input"),
    startDateDisplay: document.getElementById("start-date-display"),
    endDateDisplay: document.getElementById("end-date-display"),
    scaleBar: document.getElementById("scale-bar"),
    toggleEndLabels: document.getElementById("toggle-end-labels"),
    toggleDrawdown: document.getElementById("toggle-drawdown"),
    toggleRegimes: document.getElementById("toggle-regimes"),

    curveLegendBar: document.getElementById("curve-legend-bar"),
    chartNotices: document.getElementById("chart-notices"),
    chartStage: document.getElementById("chart-stage"),
    mainCanvas: document.getElementById("main-chart-canvas"),
    floatingHud: document.getElementById("floating-hud"),
    navigatorStage: document.getElementById("navigator-stage"),
    navigatorSpanInfo: document.getElementById("navigator-span-info"),
    navCanvas: document.getElementById("navigator-canvas"),

    summaryWindowLabel: document.getElementById("summary-window-label"),
    summaryTable: document.getElementById("summary-table"),
    summaryTbody: document.getElementById("summary-tbody"),
    toastStack: document.getElementById("toast-stack"),
  };

  let currentModel = null;
  let mainLayout = null;
  let navLayout = null;
  let rafPending = false;

  // --- Utilities ---
  function showToast(msg, isError = false) {
    const el = document.createElement("div");
    el.className = `toast-item${isError ? " error" : ""}`;
    el.textContent = msg;
    dom.toastStack.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      el.style.transition = "all 0.2s ease";
      setTimeout(() => el.remove(), 220);
    }, 3200);
  }

  function lowerBound(arr, target) {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function upperBound(arr, target) {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] <= target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function parseDateUTC(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function formatDateUTC(ms) {
    const d = new Date(ms);
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, "0");
    const day = String(d.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function daysBetween(startStr, endStr) {
    return (parseDateUTC(endStr) - parseDateUTC(startStr)) / 86400000;
  }

  function formatPct(val, digits = 2, forceSign = true) {
    if (val === null || val === undefined || Number.isNaN(val)) return "—";
    const pct = val * 100;
    const sign = forceSign && pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(digits)}%`;
  }

  function formatMult(val, digits = 2) {
    if (val === null || val === undefined || Number.isNaN(val)) return "—";
    return `${val.toFixed(digits)}x`;
  }

  function assignCatalogColors() {
    state.colorByKey.clear();
    let fallbackIdx = 0;
    for (const item of state.catalog) {
      if (FIXED_COLORS[item.key]) {
        state.colorByKey.set(item.key, FIXED_COLORS[item.key]);
      } else {
        state.colorByKey.set(
          item.key,
          FALLBACK_PALETTE[fallbackIdx % FALLBACK_PALETTE.length]
        );
        fallbackIdx += 1;
      }
    }
  }

  function displayAssetName(item) {
    return item.kind === "strategy" ? `${item.key} [策略]` : item.key;
  }

  function computePresetDateStrings(preset, minDateStr, maxDateStr) {
    const minMs = parseDateUTC(minDateStr);
    const maxMs = parseDateUTC(maxDateStr);
    const maxDt = new Date(maxMs);
    let sMs = minMs;

    if (preset === "YTD") {
      sMs = Math.max(minMs, Date.UTC(maxDt.getUTCFullYear(), 0, 1));
    } else if (preset === "近1年") {
      sMs = Math.max(minMs, maxMs - 365 * 86400000);
    } else if (preset === "近3年") {
      sMs = Math.max(minMs, maxMs - (365 * 3 + 1) * 86400000);
    } else if (preset === "近5年") {
      sMs = Math.max(minMs, maxMs - (365 * 5 + 2) * 86400000);
    } else if (preset === "近10年") {
      sMs = Math.max(minMs, maxMs - (365 * 10 + 3) * 86400000);
    } else if (preset === "全部") {
      sMs = minMs;
    }

    if (sMs >= maxMs) {
      sMs = Math.max(minMs, maxMs - 86400000);
    }
    return [formatDateUTC(sMs), maxDateStr];
  }

  // --- Data & Timeline Assembly ---
  function rebuildMasterTimeline() {
    const prevStartStr = state.masterDates[state.winStartIdx] || null;
    const prevEndStr = state.masterDates[state.winEndIdx] || null;

    const activeSeries = [];
    for (const k of state.selectedKeys) {
      const entry = state.catalogMap.get(k);
      if (entry) activeSeries.push(entry);
    }
    if (state.enableSynth && state.catalogMap.has(state.synthBase)) {
      activeSeries.push(state.catalogMap.get(state.synthBase));
    }

    const dateSet = new Set();
    for (const entry of activeSeries) {
      for (let i = 0; i < entry.dates.length; i++) {
        dateSet.add(entry.dates[i]);
      }
    }

    state.masterDates = Array.from(dateSet).sort();
    state.masterDateToIdx = new Map();
    for (let i = 0; i < state.masterDates.length; i++) {
      state.masterDateToIdx.set(state.masterDates[i], i);
    }

    const total = state.masterDates.length;
    if (total === 0) {
      state.winStartIdx = 0;
      state.winEndIdx = 0;
      return;
    }

    const globalMin = state.masterDates[0];
    const globalMax = state.masterDates[total - 1];
    dom.startDateInput.min = globalMin;
    dom.startDateInput.max = globalMax;
    dom.endDateInput.min = globalMin;
    dom.endDateInput.max = globalMax;

    if (!state.initializedWindow) {
      applyPreset("近10年", false);
      state.initializedWindow = true;
    } else if (state.preset) {
      applyPreset(state.preset, false);
    } else if (prevStartStr && prevEndStr) {
      setWindowByDateStrings(prevStartStr, prevEndStr, false);
    } else {
      state.winStartIdx = 0;
      state.winEndIdx = total - 1;
    }
  }

  function setWindowByDateStrings(startStr, endStr, clearPreset = true) {
    const total = state.masterDates.length;
    if (total < 2) {
      state.winStartIdx = 0;
      state.winEndIdx = Math.max(0, total - 1);
      return;
    }
    let sIdx = lowerBound(state.masterDates, startStr);
    let eIdx = upperBound(state.masterDates, endStr) - 1;
    sIdx = Math.max(0, Math.min(sIdx, total - 2));
    eIdx = Math.max(sIdx + 1, Math.min(eIdx, total - 1));
    if (eIdx - sIdx < Math.min(MIN_WINDOW_BARS, total - 1)) {
      if (eIdx + MIN_WINDOW_BARS < total) {
        eIdx = Math.min(total - 1, sIdx + MIN_WINDOW_BARS);
      } else {
        sIdx = Math.max(0, eIdx - MIN_WINDOW_BARS);
      }
    }
    state.winStartIdx = sIdx;
    state.winEndIdx = eIdx;
    state.measureRange = null;
    if (clearPreset) {
      state.preset = null;
      syncPresetButtons();
    }
    syncDateInputs();
  }

  function setWindowByIndices(sIdx, eIdx, clearPreset = true) {
    const total = state.masterDates.length;
    if (total < 2) return;
    const minSpan = Math.min(MIN_WINDOW_BARS, total - 1);
    let s = Math.round(sIdx);
    let e = Math.round(eIdx);
    s = Math.max(0, Math.min(s, total - 1 - minSpan));
    e = Math.max(s + minSpan, Math.min(e, total - 1));

    if (s === state.winStartIdx && e === state.winEndIdx) return;
    state.winStartIdx = s;
    state.winEndIdx = e;
    state.measureRange = null;
    if (clearPreset) {
      state.preset = null;
      syncPresetButtons();
    }
    syncDateInputs();
  }

  function applyPreset(preset, triggerRender = true) {
    const total = state.masterDates.length;
    if (total === 0) return;
    state.preset = preset;
    syncPresetButtons();
    const [sStr, eStr] = computePresetDateStrings(
      preset,
      state.masterDates[0],
      state.masterDates[total - 1]
    );
    setWindowByDateStrings(sStr, eStr, false);
    if (triggerRender) scheduleRender();
  }

  function syncPresetButtons() {
    const btns = dom.presetBar.querySelectorAll(".seg-btn");
    btns.forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.preset === state.preset);
    });
  }

  function syncDateInputs() {
    if (state.masterDates.length === 0) return;
    const s = state.masterDates[state.winStartIdx] || "";
    const e = state.masterDates[state.winEndIdx] || "";
    dom.startDateInput.value = s;
    dom.endDateInput.value = e;
    if (dom.startDateDisplay) dom.startDateDisplay.textContent = s || "—";
    if (dom.endDateDisplay) dom.endDateDisplay.textContent = e || "—";
  }

  // --- Financial Curve & Summary Computation ---
  function computeWindowModel() {
    const total = state.masterDates.length;
    if (total === 0 || state.winEndIdx < state.winStartIdx) {
      return { curves: [], viewDates: [], summaryRows: [], partials: [], wipeouts: [] };
    }

    const viewDates = state.masterDates.slice(state.winStartIdx, state.winEndIdx + 1);
    const N = viewDates.length;
    const winStartStr = viewDates[0];
    const winEndStr = viewDates[N - 1];

    const curves = [];
    const partials = [];
    const wipeouts = [];

    for (const key of state.selectedKeys) {
      const entry = state.catalogMap.get(key);
      if (!entry) continue;

      const i0 = lowerBound(entry.dates, winStartStr);
      const i1 = upperBound(entry.dates, winEndStr);
      if (i1 <= i0) continue;

      const name = displayAssetName(entry);
      const baseVal = entry.values[i0];
      const navArr = new Float64Array(N);
      navArr.fill(NaN);
      const ddArr = new Float64Array(N);
      ddArr.fill(NaN);

      const validDates = [];
      const validNavs = [];
      let peak = -Infinity;
      let lastValidIdx = -1;

      for (let i = i0; i < i1; i++) {
        const dStr = entry.dates[i];
        const mIdx = state.masterDateToIdx.get(dStr);
        if (mIdx === undefined) continue;
        const localIdx = mIdx - state.winStartIdx;
        if (localIdx < 0 || localIdx >= N) continue;

        const nav = entry.values[i] / baseVal;
        navArr[localIdx] = nav;
        if (nav > peak) peak = nav;
        ddArr[localIdx] = peak > 0 ? nav / peak - 1.0 : 0.0;

        validDates.push(dStr);
        validNavs.push(nav);
        lastValidIdx = localIdx;
      }

      if (validNavs.length === 0) continue;

      const actualStart = validDates[0];
      const isPartial = daysBetween(winStartStr, actualStart) > 5;
      if (isPartial) {
        partials.push({ name, actualStart });
      }

      curves.push({
        name,
        rawKey: key,
        kindLabel: entry.kind === "strategy" ? "策略" : "真实行情",
        kindType: entry.kind,
        color: state.colorByKey.get(key) || "#0071E3",
        dash: [],
        width: entry.kind === "strategy" ? 2.5 : 2.15,
        navArr,
        ddArr,
        validDates,
        validNavs,
        lastValidIdx,
      });
    }

    if (state.enableSynth && state.catalogMap.has(state.synthBase)) {
      const baseEntry = state.catalogMap.get(state.synthBase);
      const i0 = lowerBound(baseEntry.dates, winStartStr);
      const i1 = upperBound(baseEntry.dates, winEndStr);
      if (i1 > i0) {
        const lev = state.leverage;
        const levLabel = Number.isInteger(lev) ? String(lev) : String(Number(lev.toFixed(2)));
        const synthName = `${levLabel}x ${state.synthBase} (合成)`;

        const navArr = new Float64Array(N);
        navArr.fill(NaN);
        const ddArr = new Float64Array(N);
        ddArr.fill(NaN);

        const validDates = [];
        const validNavs = [];
        let curNav = 1.0;
        let peak = 1.0;
        let wiped = false;
        let wipeoutDate = null;
        let lastValidIdx = -1;

        for (let i = i0; i < i1; i++) {
          if (i > i0 && !wiped) {
            const prevPrice = baseEntry.values[i - 1];
            const curPrice = baseEntry.values[i];
            const dailyRet = curPrice / prevPrice - 1.0;
            const factor = 1.0 + lev * dailyRet;
            if (factor <= 0) {
              wiped = true;
              wipeoutDate = baseEntry.dates[i];
              curNav = 0.0;
            } else {
              curNav *= factor;
            }
          }

          const dStr = baseEntry.dates[i];
          const mIdx = state.masterDateToIdx.get(dStr);
          if (mIdx === undefined) continue;
          const localIdx = mIdx - state.winStartIdx;
          if (localIdx < 0 || localIdx >= N) continue;

          navArr[localIdx] = curNav;
          if (curNav > peak) peak = curNav;
          ddArr[localIdx] = peak > 0 ? curNav / peak - 1.0 : -1.0;

          validDates.push(dStr);
          validNavs.push(curNav);
          lastValidIdx = localIdx;
        }

        if (validNavs.length > 0) {
          const actualStart = validDates[0];
          if (daysBetween(winStartStr, actualStart) > 5) {
            partials.push({ name: synthName, actualStart });
          }
          if (wiped && wipeoutDate) {
            wipeouts.push({ name: synthName, wipeoutDate });
          }

          curves.push({
            name: synthName,
            rawKey: "__SYNTH__",
            kindLabel: "合成杠杆",
            kindType: "synth",
            color: SYNTH_COLOR,
            dash: [6, 4],
            width: 2.15,
            navArr,
            ddArr,
            validDates,
            validNavs,
            lastValidIdx,
          });
        }
      }
    }

    // Ensure focusCurve stays valid
    if (
      state.focusCurve !== "__ALL__" &&
      !curves.some((c) => c.name === state.focusCurve)
    ) {
      state.focusCurve = "__ALL__";
    }

    const summaryRows = curves.map((c) => {
      const m = c.validNavs.length;
      const firstDate = c.validDates[0];
      const lastDate = c.validDates[m - 1];
      const years = Math.max(daysBetween(firstDate, lastDate) / 365.25, 1.0 / 365.25);
      const finalMult = c.validNavs[m - 1] / c.validNavs[0];
      const totalRet = finalMult - 1.0;
      const cagr = finalMult <= 0 ? -1.0 : Math.pow(finalMult, 1.0 / years) - 1.0;

      const rets = [];
      let peak = -Infinity;
      let maxDd = 0.0;
      for (let i = 0; i < m; i++) {
        const v = c.validNavs[i];
        if (v > peak) peak = v;
        const dd = peak > 0 ? v / peak - 1.0 : -1.0;
        if (dd < maxDd) maxDd = dd;
        if (i > 0 && c.validNavs[i - 1] > 0) {
          const r = v / c.validNavs[i - 1] - 1.0;
          if (Number.isFinite(r)) rets.push(r);
        }
      }

      let annVol = NaN;
      if (rets.length > 1) {
        let sum = 0;
        for (let i = 0; i < rets.length; i++) sum += rets[i];
        const mean = sum / rets.length;
        let sq = 0;
        for (let i = 0; i < rets.length; i++) {
          const diff = rets[i] - mean;
          sq += diff * diff;
        }
        annVol = Math.sqrt(sq / (rets.length - 1)) * Math.sqrt(TRADING_DAYS_PER_YEAR);
      }

      return {
        name: c.name,
        color: c.color,
        kindLabel: c.kindLabel,
        kindType: c.kindType,
        rangeText: `${firstDate} ~ ${lastDate}`,
        finalMult,
        totalRet,
        cagr,
        annVol,
        maxDd,
      };
    });

    return {
      curves,
      viewDates,
      summaryRows,
      partials,
      wipeouts,
    };
  }

  // --- Nice Axis Tick Helpers ---
  function computeNiceLinearTicks(minVal, maxVal, targetCount = 6) {
    if (!Number.isFinite(minVal) || !Number.isFinite(maxVal)) return [0];
    if (Math.abs(maxVal - minVal) < 1e-6) {
      minVal -= 0.05;
      maxVal += 0.05;
    }
    const rawStep = (maxVal - minVal) / Math.max(2, targetCount);
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const residual = rawStep / mag;
    let niceStep;
    if (residual <= 1.2) niceStep = 1 * mag;
    else if (residual <= 2.5) niceStep = 2 * mag;
    else if (residual <= 5.5) niceStep = 5 * mag;
    else niceStep = 10 * mag;

    const first = Math.ceil(minVal / niceStep) * niceStep;
    const ticks = [];
    for (let v = first; v <= maxVal + niceStep * 0.001; v += niceStep) {
      ticks.push(Math.abs(v) < 1e-10 ? 0 : Number(v.toPrecision(10)));
    }
    if (minVal <= 0 && maxVal >= 0 && !ticks.some((t) => Math.abs(t) < 1e-9)) {
      ticks.push(0);
      ticks.sort((a, b) => a - b);
    }
    return ticks;
  }

  function computeNiceLogTicks(minLog, maxLog) {
    const span = maxLog - minLog;
    let mults;
    if (span <= 0.45) {
      mults = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8];
    } else if (span <= 1.25) {
      mults = [1, 1.5, 2, 3, 5, 7];
    } else if (span <= 2.6) {
      mults = [1, 2, 5];
    } else {
      mults = [1, 3];
    }

    const minExp = Math.floor(minLog) - 1;
    const maxExp = Math.ceil(maxLog) + 1;
    const ticks = [];
    for (let e = minExp; e <= maxExp; e++) {
      const base = Math.pow(10, e);
      for (const m of mults) {
        const val = base * m;
        const lv = Math.log10(val);
        if (lv >= minLog - 1e-6 && lv <= maxLog + 1e-6) {
          ticks.push(val);
        }
      }
    }
    if (minLog <= 0 && maxLog >= 0 && !ticks.some((v) => Math.abs(v - 1) < 1e-6)) {
      ticks.push(1);
      ticks.sort((a, b) => a - b);
    }
    return ticks;
  }

  function computeTimeTicks(viewDates, plotW) {
    const n = viewDates.length;
    if (n === 0) return [];
    const maxLabels = Math.max(3, Math.floor(plotW / 105));
    const totalDays = daysBetween(viewDates[0], viewDates[n - 1]);

    const ticks = [];
    if (totalDays > 520) {
      // Year boundaries
      let prevYear = null;
      const yearIndices = [];
      for (let i = 0; i < n; i++) {
        const yr = viewDates[i].slice(0, 4);
        if (yr !== prevYear) {
          yearIndices.push({ idx: i, label: yr });
          prevYear = yr;
        }
      }
      const stride = Math.max(1, Math.ceil(yearIndices.length / maxLabels));
      for (let k = 0; k < yearIndices.length; k += stride) {
        ticks.push(yearIndices[k]);
      }
    } else if (totalDays > 95) {
      // Month boundaries
      let prevYm = null;
      const monthIndices = [];
      for (let i = 0; i < n; i++) {
        const ym = viewDates[i].slice(0, 7);
        if (ym !== prevYm) {
          monthIndices.push({ idx: i, label: ym });
          prevYm = ym;
        }
      }
      const stride = Math.max(1, Math.ceil(monthIndices.length / maxLabels));
      for (let k = 0; k < monthIndices.length; k += stride) {
        ticks.push(monthIndices[k]);
      }
    } else {
      const step = Math.max(1, Math.floor((n - 1) / Math.min(maxLabels, 6)));
      for (let i = 0; i < n; i += step) {
        ticks.push({ idx: i, label: viewDates[i].slice(5) });
      }
    }
    return ticks;
  }

  function hexToRgba(hex, alpha) {
    const clean = hex.replace("#", "");
    const r = parseInt(clean.slice(0, 2), 16);
    const g = parseInt(clean.slice(2, 4), 16);
    const b = parseInt(clean.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function drawRoundedRect(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  // --- Main Chart Canvas Renderer ---
  function renderMainChart(model) {
    const rect = dom.chartStage.getBoundingClientRect();
    const cssW = Math.max(320, Math.floor(rect.width));
    const cssH = state.showDrawdown ? 548 : 428;

    const dpr = window.devicePixelRatio || 1;
    if (dom.mainCanvas.width !== Math.round(cssW * dpr) || dom.mainCanvas.height !== Math.round(cssH * dpr)) {
      dom.mainCanvas.width = Math.round(cssW * dpr);
      dom.mainCanvas.height = Math.round(cssH * dpr);
      dom.mainCanvas.style.height = `${cssH}px`;
    }

    const ctx = dom.mainCanvas.getContext("2d");
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const isLog = state.scaleMode === "log";
    const padL = 62;
    const padR = state.showEndLabels ? 148 : 22;
    const padT = 14;
    const padB = 26;
    const plotW = Math.max(80, cssW - padL - padR);
    const totalPlotH = cssH - padT - padB;

    let mainTop = padT;
    let mainH = totalPlotH;
    let ddTop = 0;
    let ddH = 0;

    if (state.showDrawdown) {
      const gap = 22;
      mainH = Math.round((totalPlotH - gap) * 0.73);
      ddTop = mainTop + mainH + gap;
      ddH = totalPlotH - mainH - gap;
    }

    const N = model.viewDates.length;
    if (N === 0 || model.curves.length === 0) {
      ctx.fillStyle = "#86868B";
      ctx.font = '500 13px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
      ctx.textAlign = "center";
      ctx.fillText("请在左侧栏选择至少一条对比曲线", cssW / 2, cssH / 2);
      ctx.restore();
      mainLayout = null;
      return;
    }

    const xOfIdx = (idx) => (N <= 1 ? padL + plotW / 2 : padL + (idx / (N - 1)) * plotW);
    const idxOfX = (x) => {
      if (N <= 1) return 0;
      const ratio = (x - padL) / plotW;
      return Math.max(0, Math.min(N - 1, Math.round(ratio * (N - 1))));
    };

    // Determine Y bounds on main pane
    let minVis = Infinity;
    let maxVis = -Infinity;
    for (const c of model.curves) {
      for (let i = 0; i < N; i++) {
        const nav = c.navArr[i];
        if (Number.isNaN(nav)) continue;
        if (isLog) {
          if (nav <= 0) continue;
          const lv = Math.log10(nav);
          if (lv < minVis) minVis = lv;
          if (lv > maxVis) maxVis = lv;
        } else {
          const rv = nav - 1.0;
          if (rv < minVis) minVis = rv;
          if (rv > maxVis) maxVis = rv;
        }
      }
    }

    if (!Number.isFinite(minVis) || !Number.isFinite(maxVis)) {
      minVis = isLog ? -0.3 : -0.2;
      maxVis = isLog ? 0.5 : 0.5;
    }
    if (Math.abs(maxVis - minVis) < 1e-5) {
      minVis -= 0.08;
      maxVis += 0.08;
    }
    const yPad = (maxVis - minVis) * 0.075;
    const visLow = minVis - yPad;
    const visHigh = maxVis + yPad;

    const yOfMainVis = (v) => mainTop + ((visHigh - v) / (visHigh - visLow)) * mainH;
    const yOfNav = (nav) => {
      if (isLog) {
        if (nav <= 0) return mainTop + mainH;
        return yOfMainVis(Math.log10(nav));
      }
      return yOfMainVis(nav - 1.0);
    };

    // Determine Y bounds on drawdown pane
    let minDd = -0.15;
    if (state.showDrawdown) {
      let rawMinDd = 0;
      for (const c of model.curves) {
        for (let i = 0; i < N; i++) {
          const d = c.ddArr[i];
          if (!Number.isNaN(d) && d < rawMinDd) rawMinDd = d;
        }
      }
      minDd = Math.min(-0.08, rawMinDd * 1.08);
      minDd = Math.max(-1.02, minDd);
    }
    const yOfDd = (ddVal) => ddTop + ((0.02 - ddVal) / (0.02 - minDd)) * ddH;

    mainLayout = {
      cssW,
      cssH,
      padL,
      padR,
      padT,
      padB,
      plotW,
      mainTop,
      mainH,
      ddTop,
      ddH,
      N,
      xOfIdx,
      idxOfX,
      yOfNav,
      yOfDd,
    };

    // 1. Regime Shaded Bands
    if (state.showRegimes) {
      const wStart = model.viewDates[0];
      const wEnd = model.viewDates[N - 1];
      for (const reg of MARKET_REGIMES) {
        const ovStart = reg.start > wStart ? reg.start : wStart;
        const ovEnd = reg.end < wEnd ? reg.end : wEnd;
        if (ovStart < ovEnd) {
          const iStart = lowerBound(model.viewDates, ovStart);
          const iEnd = Math.max(iStart, upperBound(model.viewDates, ovEnd) - 1);
          const rx1 = xOfIdx(iStart);
          const rx2 = Math.max(rx1 + 2, xOfIdx(iEnd));
          ctx.fillStyle = reg.fill;
          ctx.fillRect(rx1, mainTop, rx2 - rx1, mainH);
          if (state.showDrawdown) {
            ctx.fillRect(rx1, ddTop, rx2 - rx1, ddH);
          }
          if (rx2 - rx1 > 36) {
            ctx.fillStyle = "#6E6E73";
            ctx.font = '500 10.5px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
            ctx.textAlign = "left";
            ctx.fillText(reg.label, rx1 + 5, mainTop + 13);
          }
        }
      }
    }

    // 2. Horizontal Grid & Y-Axis Ticks (Main Pane)
    ctx.font = '500 11px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    if (isLog) {
      const logTicks = computeNiceLogTicks(visLow, visHigh);
      for (const multVal of logTicks) {
        const py = yOfMainVis(Math.log10(multVal));
        if (py < mainTop - 2 || py > mainTop + mainH + 2) continue;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.055)";
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(padL, py);
        ctx.lineTo(padL + plotW, py);
        ctx.stroke();

        const lbl =
          multVal >= 10
            ? `${Math.round(multVal)}x`
            : multVal >= 1
            ? `${Number(multVal.toFixed(2))}x`
            : `${Number(multVal.toFixed(2))}x`;
        ctx.fillStyle = "#86868B";
        ctx.fillText(lbl, padL - 8, py);
      }
    } else {
      const linTicks = computeNiceLinearTicks(visLow, visHigh, 6);
      for (const rv of linTicks) {
        const py = yOfMainVis(rv);
        if (py < mainTop - 2 || py > mainTop + mainH + 2) continue;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.055)";
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(padL, py);
        ctx.lineTo(padL + plotW, py);
        ctx.stroke();

        const pct = rv * 100;
        const decimals = Math.abs(visHigh - visLow) < 0.25 ? 1 : 0;
        const sign = pct > 0 ? "+" : "";
        const numTxt = Number(pct.toFixed(decimals)).toLocaleString("en-US", {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        });
        ctx.fillStyle = "#86868B";
        ctx.fillText(`${sign}${numTxt}%`, padL - 8, py);
      }
    }

    // Baseline (0% or 1.00x)
    const baseVis = 0.0;
    if (baseVis >= visLow && baseVis <= visHigh) {
      const baseY = yOfMainVis(baseVis);
      ctx.strokeStyle = "rgba(110, 110, 115, 0.42)";
      ctx.lineWidth = 1.1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(padL, baseY);
      ctx.lineTo(padL + plotW, baseY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 3. Drawdown Grid & Y-Axis Ticks
    if (state.showDrawdown) {
      const ddTicks = computeNiceLinearTicks(minDd, 0, 3);
      for (const dv of ddTicks) {
        const py = yOfDd(dv);
        if (py < ddTop - 2 || py > ddTop + ddH + 2) continue;
        ctx.strokeStyle = Math.abs(dv) < 1e-9 ? "rgba(110, 110, 115, 0.35)" : "rgba(0, 0, 0, 0.05)";
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(padL, py);
        ctx.lineTo(padL + plotW, py);
        ctx.stroke();

        ctx.fillStyle = "#86868B";
        ctx.textAlign = "right";
        ctx.fillText(`${(dv * 100).toFixed(0)}%`, padL - 8, py);
      }

      ctx.fillStyle = "#86868B";
      ctx.font = '600 10.5px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
      ctx.textAlign = "left";
      ctx.fillText("水下回撤", padL + 4, ddTop - 9);
    }

    // 4. Sort curves so spotlighted curve draws on top
    const orderedCurves = [...model.curves].sort((a, b) => {
      const aFocus = state.focusCurve === "__ALL__" || state.focusCurve === a.name ? 1 : 0;
      const bFocus = state.focusCurve === "__ALL__" || state.focusCurve === b.name ? 1 : 0;
      return aFocus - bFocus;
    });

    // Clip to plot area for curves
    ctx.save();
    ctx.beginPath();
    ctx.rect(padL, mainTop, plotW, state.showDrawdown ? ddTop + ddH - mainTop : mainH);
    ctx.clip();

    for (const c of orderedCurves) {
      const isFocused = state.focusCurve === "__ALL__" || state.focusCurve === c.name;
      const alpha = isFocused ? 1.0 : 0.16;
      const strokeW =
        state.focusCurve === c.name ? c.width + 0.85 : isFocused ? c.width : 1.25;

      // Optional subtle gradient breath fill on main pane
      const useSubtleFill =
        !isLog &&
        ((state.focusCurve === c.name) ||
          (state.focusCurve === "__ALL__" && c.rawKey === "QQQ"));

      if (useSubtleFill) {
        const zeroY = Math.max(mainTop, Math.min(mainTop + mainH, yOfMainVis(0)));
        const grad = ctx.createLinearGradient(0, mainTop, 0, mainTop + mainH);
        grad.addColorStop(0, hexToRgba(c.color, 0.11));
        grad.addColorStop(1, hexToRgba(c.color, 0.0));

        ctx.beginPath();
        let segStartX = null;
        let prevX = null;
        for (let i = 0; i < N; i++) {
          const nav = c.navArr[i];
          if (Number.isNaN(nav)) continue;
          const px = xOfIdx(i);
          const py = yOfNav(nav);
          if (segStartX === null) {
            segStartX = px;
            ctx.moveTo(px, zeroY);
            ctx.lineTo(px, py);
          } else {
            ctx.lineTo(px, py);
          }
          prevX = px;
        }
        if (segStartX !== null && prevX !== null) {
          ctx.lineTo(prevX, zeroY);
          ctx.closePath();
          ctx.fillStyle = grad;
          ctx.fill();
        }
      }

      // Main curve stroke
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = c.color;
      ctx.lineWidth = strokeW;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.setLineDash(c.dash);

      ctx.beginPath();
      let penDown = false;
      for (let i = 0; i < N; i++) {
        const nav = c.navArr[i];
        if (Number.isNaN(nav) || (isLog && nav <= 0)) {
          penDown = false;
          continue;
        }
        const px = xOfIdx(i);
        const py = yOfNav(nav);
        if (!penDown) {
          ctx.moveTo(px, py);
          penDown = true;
        } else {
          ctx.lineTo(px, py);
        }
      }
      ctx.stroke();
      ctx.restore();

      // Drawdown curve
      if (state.showDrawdown) {
        const fillDd =
          isFocused &&
          (state.focusCurve === c.name ||
            (state.focusCurve === "__ALL__" && c.rawKey === "QQQ"));

        if (fillDd) {
          const topZeroY = yOfDd(0);
          ctx.beginPath();
          let started = false;
          let lastPx = null;
          for (let i = 0; i < N; i++) {
            const d = c.ddArr[i];
            if (Number.isNaN(d)) continue;
            const px = xOfIdx(i);
            const py = yOfDd(d);
            if (!started) {
              ctx.moveTo(px, topZeroY);
              ctx.lineTo(px, py);
              started = true;
            } else {
              ctx.lineTo(px, py);
            }
            lastPx = px;
          }
          if (started && lastPx !== null) {
            ctx.lineTo(lastPx, topZeroY);
            ctx.closePath();
            ctx.fillStyle = hexToRgba(c.color, 0.085);
            ctx.fill();
          }
        }

        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = c.color;
        ctx.lineWidth = Math.max(1.15, strokeW - 0.55);
        ctx.lineJoin = "round";
        ctx.setLineDash(c.dash);
        ctx.beginPath();
        let ddPen = false;
        for (let i = 0; i < N; i++) {
          const d = c.ddArr[i];
          if (Number.isNaN(d)) {
            ddPen = false;
            continue;
          }
          const px = xOfIdx(i);
          const py = yOfDd(d);
          if (!ddPen) {
            ctx.moveTo(px, py);
            ddPen = true;
          } else {
            ctx.lineTo(px, py);
          }
        }
        ctx.stroke();
        ctx.restore();
      }
    }
    ctx.restore();

    // 5. X-Axis Date Labels
    const xAxisY = (state.showDrawdown ? ddTop + ddH : mainTop + mainH) + 15;
    const xTicks = computeTimeTicks(model.viewDates, plotW);
    ctx.font = '500 11px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
    ctx.fillStyle = "#86868B";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const tk of xTicks) {
      const px = xOfIdx(tk.idx);
      ctx.fillText(tk.label, px, xAxisY);
    }

    // 6. Anti-Collision End Labels in Right Gutter
    if (state.showEndLabels) {
      const items = [];
      for (const c of model.curves) {
        if (c.lastValidIdx < 0) continue;
        const lastNav = c.navArr[c.lastValidIdx];
        if (Number.isNaN(lastNav) || (isLog && lastNav <= 0)) continue;
        const anchorX = xOfIdx(c.lastValidIdx);
        const anchorY = yOfNav(lastNav);
        const valText = isLog ? formatMult(lastNav, 2) : formatPct(lastNav - 1.0, 1);
        const shortName = c.name.length > 12 ? `${c.name.slice(0, 11)}…` : c.name;
        const isFocused = state.focusCurve === "__ALL__" || state.focusCurve === c.name;
        items.push({
          text: `${shortName} ${valText}`,
          anchorX,
          anchorY,
          y: anchorY,
          color: c.color,
          dimmed: !isFocused,
        });
      }

      const minGap = 21;
      items.sort((a, b) => a.y - b.y);
      for (let i = 1; i < items.length; i++) {
        if (items[i].y - items[i - 1].y < minGap) {
          items[i].y = items[i - 1].y + minGap;
        }
      }
      const bottomLimit = mainTop + mainH - 8;
      if (items.length > 0 && items[items.length - 1].y > bottomLimit) {
        const shift = items[items.length - 1].y - bottomLimit;
        for (const it of items) it.y -= shift;
        if (items[0].y < mainTop + 8) {
          items[0].y = mainTop + 8;
          for (let i = 1; i < items.length; i++) {
            if (items[i].y - items[i - 1].y < minGap) {
              items[i].y = items[i - 1].y + minGap;
            }
          }
        }
      }

      const pillX = padL + plotW + 10;
      const maxPillW = padR - 14;
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif';

      for (const it of items) {
        ctx.save();
        ctx.globalAlpha = it.dimmed ? 0.28 : 0.98;

        // Delicate connector line
        ctx.strokeStyle = hexToRgba(it.color, 0.45);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(it.anchorX, it.anchorY);
        ctx.lineTo(pillX - 3, it.y);
        ctx.stroke();

        // Anchor dot
        ctx.fillStyle = it.color;
        ctx.beginPath();
        ctx.arc(it.anchorX, it.anchorY, 2.6, 0, Math.PI * 2);
        ctx.fill();

        // Badge pill
        const textW = Math.min(maxPillW - 12, ctx.measureText(it.text).width);
        const boxW = textW + 12;
        const boxH = 19;
        drawRoundedRect(ctx, pillX, it.y - boxH / 2, boxW, boxH, 6);
        ctx.fillStyle = hexToRgba(it.color, 0.085);
        ctx.fill();
        ctx.strokeStyle = hexToRgba(it.color, 0.28);
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = it.color;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(it.text, pillX + 6, it.y + 0.5, maxPillW - 12);
        ctx.restore();
      }
    }

    // 7. Shift+Drag Measurement Ruler Overlay
    if (state.measureRange) {
      const mStart = Math.min(state.measureRange.startIdx, state.measureRange.endIdx);
      const mEnd = Math.max(state.measureRange.startIdx, state.measureRange.endIdx);
      if (mEnd > mStart) {
        const mx1 = xOfIdx(mStart);
        const mx2 = xOfIdx(mEnd);
        ctx.save();
        ctx.fillStyle = "rgba(0, 113, 227, 0.075)";
        ctx.fillRect(mx1, mainTop, mx2 - mx1, mainH);
        ctx.strokeStyle = "rgba(0, 113, 227, 0.55)";
        ctx.lineWidth = 1.2;
        ctx.setLineDash([4, 3]);
        ctx.strokeRect(mx1, mainTop, mx2 - mx1, mainH);
        ctx.setLineDash([]);

        // Measure callout box
        const d1 = model.viewDates[mStart];
        const d2 = model.viewDates[mEnd];
        const calDays = Math.round(daysBetween(d1, d2));
        const trDays = mEnd - mStart;
        const lines = [`区间测距: ${d1} → ${d2} (${trDays} 交易日 / ${calDays} 天)`];
        for (const c of model.curves) {
          const v1 = c.navArr[mStart];
          const v2 = c.navArr[mEnd];
          if (!Number.isNaN(v1) && !Number.isNaN(v2) && v1 > 0) {
            const subRet = v2 / v1 - 1.0;
            lines.push(`${c.name}: ${formatPct(subRet, 2)}`);
          }
        }

        ctx.font = '600 11.5px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
        let boxW = 0;
        for (const ln of lines) {
          boxW = Math.max(boxW, ctx.measureText(ln).width);
        }
        boxW += 20;
        const boxH = lines.length * 18 + 12;
        let bx = (mx1 + mx2) / 2 - boxW / 2;
        bx = Math.max(padL + 6, Math.min(padL + plotW - boxW - 6, bx));
        const by = mainTop + 10;

        drawRoundedRect(ctx, bx, by, boxW, boxH, 10);
        ctx.fillStyle = "rgba(29, 29, 31, 0.90)";
        ctx.fill();

        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        lines.forEach((ln, idx) => {
          ctx.fillStyle = idx === 0 ? "#8EC5FF" : "#FFFFFF";
          ctx.fillText(ln, bx + 10, by + 15 + idx * 18);
        });
        ctx.restore();
      }
    }

    // 8. Interactive Crosshair & Hover Dots
    if (state.hoverIdx !== null && state.hoverIdx >= 0 && state.hoverIdx < N) {
      const hIdx = state.hoverIdx;
      const hx = xOfIdx(hIdx);
      const bottomY = state.showDrawdown ? ddTop + ddH : mainTop + mainH;

      ctx.save();
      ctx.strokeStyle = "rgba(0, 113, 227, 0.42)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(hx, mainTop);
      ctx.lineTo(hx, bottomY);
      ctx.stroke();
      ctx.setLineDash([]);

      for (const c of orderedCurves) {
        const isFocused = state.focusCurve === "__ALL__" || state.focusCurve === c.name;
        if (!isFocused) continue;
        const nav = c.navArr[hIdx];
        if (!Number.isNaN(nav) && !(isLog && nav <= 0)) {
          const hy = yOfNav(nav);
          ctx.fillStyle = "#FFFFFF";
          ctx.beginPath();
          ctx.arc(hx, hy, 5, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = c.color;
          ctx.beginPath();
          ctx.arc(hx, hy, 3.4, 0, Math.PI * 2);
          ctx.fill();
        }
        if (state.showDrawdown) {
          const dd = c.ddArr[hIdx];
          if (!Number.isNaN(dd)) {
            const ddy = yOfDd(dd);
            ctx.fillStyle = c.color;
            ctx.beginPath();
            ctx.arc(hx, ddy, 2.8, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      // X-axis date badge
      const dateStr = model.viewDates[hIdx];
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
      const dw = ctx.measureText(dateStr).width + 14;
      const dx = Math.max(padL, Math.min(padL + plotW - dw, hx - dw / 2));
      drawRoundedRect(ctx, dx, bottomY + 4, dw, 20, 6);
      ctx.fillStyle = "#1D1D1F";
      ctx.fill();
      ctx.fillStyle = "#FFFFFF";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(dateStr, dx + dw / 2, bottomY + 14.5);
      ctx.restore();

      updateFloatingHud(model, hIdx, hx, padL, plotW);
    } else {
      dom.floatingHud.classList.add("hidden");
    }

    ctx.restore();
  }

  function updateFloatingHud(model, hIdx, hx, padL, plotW) {
    if (state.measureRange) {
      dom.floatingHud.classList.add("hidden");
      return;
    }
    const dateStr = model.viewDates[hIdx];
    const isLog = state.scaleMode === "log";
    const rows = [];

    for (const c of model.curves) {
      const nav = c.navArr[hIdx];
      if (Number.isNaN(nav)) continue;
      const dd = c.ddArr[hIdx];
      rows.push({
        name: c.name,
        color: c.color,
        nav,
        valTxt: isLog ? formatMult(nav, 2) : formatPct(nav - 1.0, 2),
        ddTxt: !Number.isNaN(dd) ? formatPct(dd, 1, false) : "",
      });
    }

    if (rows.length === 0) {
      dom.floatingHud.classList.add("hidden");
      return;
    }

    rows.sort((a, b) => b.nav - a.nav);

    let html = `<div class="hud-date"><span>${dateStr}</span><span>${isLog ? "净值倍数 / 回撤" : "累计收益 / 回撤"}</span></div><div class="hud-rows">`;
    for (const r of rows) {
      html += `
        <div class="hud-row">
          <div class="hud-row-left">
            <span class="hud-dot" style="background:${r.color}"></span>
            <span class="hud-label">${r.name}</span>
          </div>
          <div class="hud-metrics">
            <span class="hud-main-val">${r.valTxt}</span>
            ${state.showDrawdown && r.ddTxt ? `<span class="hud-dd-val">${r.ddTxt}</span>` : ""}
          </div>
        </div>
      `;
    }
    html += `</div>`;
    dom.floatingHud.innerHTML = html;
    dom.floatingHud.classList.remove("hidden");

    const hudW = dom.floatingHud.offsetWidth || 230;
    let leftPos = hx + 16;
    if (leftPos + hudW > padL + plotW - 8) {
      leftPos = Math.max(padL + 8, hx - hudW - 16);
    }
    dom.floatingHud.style.left = `${leftPos}px`;
  }

  // --- TradingView-Style Bottom Timeline Ruler Renderer ---
  function renderNavigator() {
    const rect = dom.navigatorStage.getBoundingClientRect();
    const cssW = Math.max(320, Math.floor(rect.width));
    const cssH = 72;
    const dpr = window.devicePixelRatio || 1;

    if (dom.navCanvas.width !== Math.round(cssW * dpr) || dom.navCanvas.height !== Math.round(cssH * dpr)) {
      dom.navCanvas.width = Math.round(cssW * dpr);
      dom.navCanvas.height = Math.round(cssH * dpr);
    }

    const ctx = dom.navCanvas.getContext("2d");
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const M = state.masterDates.length;
    if (M < 2) {
      ctx.restore();
      navLayout = null;
      return;
    }

    if (dom.navigatorSpanInfo) {
      const sD = state.masterDates[state.winStartIdx];
      const eD = state.masterDates[state.winEndIdx];
      const yrs = (daysBetween(sD, eD) / 365.25).toFixed(1);
      dom.navigatorSpanInfo.textContent = `${sD} — ${eD} (${yrs} 年) · 拖动边缘选时段 / 按住选区左右滑动`;
    }

    const padL = 6;
    const padR = 6;
    const trackW = Math.max(60, cssW - padL - padR);
    const chartTop = 3;
    const chartH = 46;
    const rulerTop = chartTop + chartH;
    const rulerH = cssH - rulerTop;

    const xOfMaster = (mIdx) => padL + (mIdx / (M - 1)) * trackW;
    const masterOfX = (x) => {
      const ratio = (x - padL) / trackW;
      return Math.max(0, Math.min(M - 1, Math.round(ratio * (M - 1))));
    };

    const selX1 = xOfMaster(state.winStartIdx);
    const selX2 = Math.max(selX1 + 8, xOfMaster(state.winEndIdx));

    navLayout = {
      cssW,
      cssH,
      padL,
      padR,
      trackW,
      chartTop,
      chartH,
      selX1,
      selX2,
      M,
      xOfMaster,
      masterOfX,
    };

    // Track recessed background
    drawRoundedRect(ctx, padL, chartTop, trackW, chartH, 9);
    ctx.fillStyle = "#EEF0F5";
    ctx.fill();

    // Reference overview curve (QQQ if available, else first active series)
    let refEntry = state.catalogMap.get("QQQ");
    if (!refEntry || !state.selectedKeys.includes("QQQ")) {
      const firstKey = state.selectedKeys[0] || (state.enableSynth ? state.synthBase : null);
      if (firstKey && state.catalogMap.has(firstKey)) {
        refEntry = state.catalogMap.get(firstKey);
      }
    }

    let curvePts = [];
    let yOfLv = null;
    if (refEntry && refEntry.values.length > 1) {
      let minLv = Infinity;
      let maxLv = -Infinity;
      for (let i = 0; i < refEntry.dates.length; i++) {
        const mIdx = state.masterDateToIdx.get(refEntry.dates[i]);
        if (mIdx === undefined) continue;
        const lv = Math.log(Math.max(refEntry.values[i], 1e-8));
        if (lv < minLv) minLv = lv;
        if (lv > maxLv) maxLv = lv;
        curvePts.push({ x: xOfMaster(mIdx), lv });
      }
      if (curvePts.length > 1) {
        const span = Math.max(maxLv - minLv, 1e-5);
        yOfLv = (lv) => chartTop + 6 + ((maxLv - lv) / span) * (chartH - 11);
      }
    }

    // Layer 1: Muted full-history curve across unselected background
    if (curvePts.length > 1 && yOfLv) {
      ctx.save();
      drawRoundedRect(ctx, padL, chartTop, trackW, chartH, 9);
      ctx.clip();

      ctx.beginPath();
      ctx.moveTo(curvePts[0].x, chartTop + chartH);
      for (const p of curvePts) ctx.lineTo(p.x, yOfLv(p.lv));
      ctx.lineTo(curvePts[curvePts.length - 1].x, chartTop + chartH);
      ctx.closePath();
      ctx.fillStyle = "rgba(134, 134, 139, 0.10)";
      ctx.fill();

      ctx.beginPath();
      ctx.moveTo(curvePts[0].x, yOfLv(curvePts[0].lv));
      for (let i = 1; i < curvePts.length; i++) ctx.lineTo(curvePts[i].x, yOfLv(curvePts[i].lv));
      ctx.strokeStyle = "rgba(110, 110, 115, 0.38)";
      ctx.lineWidth = 1.2;
      ctx.lineJoin = "round";
      ctx.stroke();
      ctx.restore();
    }

    // Layer 2: Illuminated Pure White Active Window + Vibrant Curve Inside
    const isActiveHover = Boolean(
      state.navDrag ||
        state.navHoverZone === "window" ||
        state.navHoverZone === "left" ||
        state.navHoverZone === "right"
    );

    ctx.save();
    ctx.shadowColor = "rgba(0, 113, 227, 0.10)";
    ctx.shadowBlur = 8;
    ctx.shadowOffsetY = 1;
    drawRoundedRect(ctx, selX1, chartTop, selX2 - selX1, chartH, 7);
    ctx.fillStyle = "#FFFFFF";
    ctx.fill();
    ctx.restore();

    if (curvePts.length > 1 && yOfLv) {
      ctx.save();
      drawRoundedRect(ctx, selX1, chartTop, selX2 - selX1, chartH, 7);
      ctx.clip();

      const grad = ctx.createLinearGradient(0, chartTop, 0, chartTop + chartH);
      grad.addColorStop(0, "rgba(0, 113, 227, 0.18)");
      grad.addColorStop(1, "rgba(0, 113, 227, 0.01)");

      ctx.beginPath();
      ctx.moveTo(curvePts[0].x, chartTop + chartH);
      for (const p of curvePts) ctx.lineTo(p.x, yOfLv(p.lv));
      ctx.lineTo(curvePts[curvePts.length - 1].x, chartTop + chartH);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      ctx.moveTo(curvePts[0].x, yOfLv(curvePts[0].lv));
      for (let i = 1; i < curvePts.length; i++) ctx.lineTo(curvePts[i].x, yOfLv(curvePts[i].lv));
      ctx.strokeStyle = "#0071E3";
      ctx.lineWidth = 1.55;
      ctx.lineJoin = "round";
      ctx.stroke();
      ctx.restore();
    }

    // Precision Ruler Ticks Along Bottom
    let prevYr = null;
    let prevQuarter = null;
    const yearTicks = [];
    const minorTicks = [];
    for (let i = 0; i < M; i++) {
      const yr = state.masterDates[i].slice(0, 4);
      const mo = Number(state.masterDates[i].slice(5, 7));
      const q = `${yr}-Q${Math.floor((mo - 1) / 3) + 1}`;
      if (yr !== prevYr) {
        yearTicks.push({ mIdx: i, yr });
        prevYr = yr;
        prevQuarter = q;
      } else if (q !== prevQuarter) {
        minorTicks.push(i);
        prevQuarter = q;
      }
    }

    ctx.strokeStyle = "rgba(0, 0, 0, 0.075)";
    ctx.lineWidth = 1;
    for (const mIdx of minorTicks) {
      const qx = xOfMaster(mIdx);
      ctx.beginPath();
      ctx.moveTo(qx, rulerTop + 1);
      ctx.lineTo(qx, rulerTop + 4);
      ctx.stroke();
    }

    const yrStride = Math.max(1, Math.ceil(yearTicks.length / Math.max(4, Math.floor(trackW / 68))));
    ctx.font = '500 10.5px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";

    for (let k = 0; k < yearTicks.length; k += yrStride) {
      const tx = xOfMaster(yearTicks[k].mIdx);
      ctx.strokeStyle = "rgba(0, 0, 0, 0.18)";
      ctx.beginPath();
      ctx.moveTo(tx, rulerTop + 1);
      ctx.lineTo(tx, rulerTop + 6.5);
      ctx.stroke();

      if (tx + 30 < padL + trackW) {
        ctx.fillStyle = "#86868B";
        ctx.fillText(yearTicks[k].yr, tx + 4, rulerTop + rulerH / 2 + 1.5);
      }
    }

    // Active Window Frame Border & Top Grip Pill
    drawRoundedRect(ctx, selX1, chartTop + 0.5, selX2 - selX1, chartH - 1, 7);
    ctx.strokeStyle = isActiveHover ? "#0071E3" : "rgba(0, 113, 227, 0.55)";
    ctx.lineWidth = isActiveHover ? 1.6 : 1.25;
    ctx.stroke();

    if (selX2 - selX1 > 36) {
      const midX = (selX1 + selX2) / 2;
      drawRoundedRect(ctx, midX - 13, chartTop + 4, 26, 3, 1.5);
      ctx.fillStyle = isActiveHover ? "rgba(0, 113, 227, 0.50)" : "rgba(0, 113, 227, 0.22)";
      ctx.fill();
    }

    // Left & Right Sculpted Capsule Handles
    const drawHandle = (hx, highlighted) => {
      const hw = 10;
      const hh = 28;
      const hy = chartTop + (chartH - hh) / 2;
      ctx.save();
      ctx.shadowColor = "rgba(0, 0, 0, 0.15)";
      ctx.shadowBlur = 5;
      ctx.shadowOffsetY = 1;
      drawRoundedRect(ctx, hx - hw / 2, hy, hw, hh, 5);
      ctx.fillStyle = highlighted ? "#0071E3" : "#FFFFFF";
      ctx.fill();
      ctx.restore();

      drawRoundedRect(ctx, hx - hw / 2, hy, hw, hh, 5);
      ctx.strokeStyle = "#0071E3";
      ctx.lineWidth = 1.3;
      ctx.stroke();

      ctx.strokeStyle = highlighted ? "#FFFFFF" : "#0071E3";
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.moveTo(hx - 1.5, hy + 8);
      ctx.lineTo(hx - 1.5, hy + hh - 8);
      ctx.moveTo(hx + 1.5, hy + 8);
      ctx.lineTo(hx + 1.5, hy + hh - 8);
      ctx.stroke();
    };

    const leftActive =
      (state.navDrag && state.navDrag.mode === "left") || state.navHoverZone === "left";
    const rightActive =
      (state.navDrag && state.navDrag.mode === "right") || state.navHoverZone === "right";
    drawHandle(selX1, leftActive);
    drawHandle(selX2, rightActive);

    // Date Callout Badges When Scrubbing or Hovering Navigator
    if (state.navDrag || state.navHoverZone) {
      const sDate = state.masterDates[state.winStartIdx];
      const eDate = state.masterDates[state.winEndIdx];
      ctx.font = '600 10.5px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
      const labelTxt = `${sDate} — ${eDate}`;
      const bw = ctx.measureText(labelTxt).width + 14;
      let bx = (selX1 + selX2) / 2 - bw / 2;
      bx = Math.max(padL + 4, Math.min(padL + trackW - bw - 4, bx));
      const by = chartTop + chartH - 19;

      drawRoundedRect(ctx, bx, by, bw, 16, 5);
      ctx.fillStyle = "rgba(29, 29, 31, 0.84)";
      ctx.fill();
      ctx.fillStyle = "#FFFFFF";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(labelTxt, bx + bw / 2, by + 8.5);
    }

    ctx.restore();
  }

  // --- DOM UI Sync ---
  function renderSidebarCatalog() {
    dom.selectedCountBadge.textContent = String(state.selectedKeys.length);

    dom.assetList.innerHTML = "";
    for (const item of state.catalog) {
      const isSelected = state.selectedKeys.includes(item.key);
      const color = state.colorByKey.get(item.key) || "#0071E3";
      const row = document.createElement("div");
      row.className = `asset-item${isSelected ? " selected" : ""}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(isSelected));
      row.innerHTML = `
        <div class="asset-item-left">
          <span class="asset-dot" style="${isSelected ? `background:${color}` : ""}"></span>
          <span class="asset-name">${item.key}</span>
          ${item.kind === "strategy" ? '<span class="asset-kind-tag">策略</span>' : ""}
        </div>
        <span class="asset-check">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M2.5 7.2L5.6 10.3L11.5 3.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
      `;
      row.addEventListener("click", () => {
        const idx = state.selectedKeys.indexOf(item.key);
        if (idx >= 0) {
          state.selectedKeys.splice(idx, 1);
        } else {
          state.selectedKeys.push(item.key);
        }
        rebuildMasterTimeline();
        renderSidebarCatalog();
        scheduleRender();
      });
      dom.assetList.appendChild(row);
    }

    // Synth base select
    const prevBase = state.synthBase;
    dom.synthBaseSelect.innerHTML = "";
    for (const item of state.catalog) {
      const opt = document.createElement("option");
      opt.value = item.key;
      opt.textContent = displayAssetName(item);
      dom.synthBaseSelect.appendChild(opt);
    }
    if (state.catalogMap.has(prevBase)) {
      state.synthBase = prevBase;
    } else if (state.catalogMap.has("QQQ")) {
      state.synthBase = "QQQ";
    } else if (state.catalog.length > 0) {
      state.synthBase = state.catalog[0].key;
    }
    dom.synthBaseSelect.value = state.synthBase;

    // Show/hide market refresh block
    const hasSelectedMarket = state.selectedKeys.some(
      (k) => state.catalogMap.get(k)?.kind === "market"
    );
    dom.refreshBlock.classList.toggle("hidden", !hasSelectedMarket);

    // Parse errors
    if (state.parseErrors.length > 0) {
      dom.parseWarning.textContent = `部分 CSV 未加载：${state.parseErrors.join(" | ")}`;
      dom.parseWarning.classList.remove("hidden");
    } else {
      dom.parseWarning.classList.add("hidden");
    }
  }

  function renderTopStatusAndLegend(model) {
    const activeCount = model.curves.length;
    const latestDate =
      state.masterDates.length > 0
        ? state.masterDates[state.masterDates.length - 1]
        : "—";
    dom.statusText.textContent = `当前对比 ${activeCount} 条曲线 · 最新数据至 ${latestDate}`;

    // Curve Legend Strip
    const isLog = state.scaleMode === "log";
    const refIdx = state.hoverIdx !== null ? state.hoverIdx : null;
    dom.curveLegendBar.innerHTML = "";
    for (const c of model.curves) {
      const idx = refIdx !== null ? refIdx : c.lastValidIdx;
      const nav = idx >= 0 ? c.navArr[idx] : NaN;
      const valTxt = Number.isNaN(nav)
        ? "—"
        : isLog
        ? formatMult(nav, 2)
        : formatPct(nav - 1.0, 2);

      const isSingleFocus = state.focusCurve === c.name;
      const isDimmed = state.focusCurve !== "__ALL__" && !isSingleFocus;
      const pill = document.createElement("div");
      pill.className = `legend-pill${isSingleFocus ? " focused" : ""}${isDimmed ? " dimmed" : ""}`;
      pill.title = "点击聚焦或恢复均衡显示";
      const isDashed = c.dash && c.dash.length > 0;
      pill.innerHTML = `
        <span class="legend-swatch${isDashed ? " dashed" : ""}" style="${
          isDashed ? `--swatch-color:${c.color}` : `background:${c.color}`
        }"></span>
        <span class="legend-name">${c.name}</span>
        <span class="legend-val">${valTxt}</span>
      `;
      pill.addEventListener("click", () => {
        state.focusCurve = state.focusCurve === c.name ? "__ALL__" : c.name;
        scheduleRender();
      });
      dom.curveLegendBar.appendChild(pill);
    }

    // Notices
    const noticeHtml = [];
    if (model.partials.length > 0) {
      const pTxt = model.partials
        .map((p) => `<strong>${p.name}</strong> (${p.actualStart} 起)`)
        .join("、");
      noticeHtml.push(
        `<div class="notice-line">注：以下曲线晚于所选开始日期，按其区间内首个交易日归零起算：${pTxt}。</div>`
      );
    }
    if (model.wipeouts.length > 0) {
      const wTxt = model.wipeouts
        .map((w) => `<strong>${w.name}</strong> 在 ${w.wipeoutDate} 触发单日归零`)
        .join("；");
      noticeHtml.push(`<div class="notice-line warn">${wTxt}（已按清零截断）。</div>`);
    }
    dom.chartNotices.innerHTML = noticeHtml.join("");
  }

  function renderSummaryTable(model) {
    if (model.viewDates.length > 0) {
      const d0 = model.viewDates[0];
      const d1 = model.viewDates[model.viewDates.length - 1];
      dom.summaryWindowLabel.textContent = `${d0} 至 ${d1} · 共 ${model.viewDates.length} 个交易日`;
    } else {
      dom.summaryWindowLabel.textContent = "";
    }

    const rows = [...model.summaryRows];
    if (state.sortKey) {
      const k = state.sortKey;
      const dir = state.sortAsc ? 1 : -1;
      rows.sort((a, b) => {
        const va = a[k];
        const vb = b[k];
        if (typeof va === "string") return va.localeCompare(vb, "zh-CN") * dir;
        if (Number.isNaN(va)) return 1;
        if (Number.isNaN(vb)) return -1;
        return (va - vb) * dir;
      });
    }

    dom.summaryTbody.innerHTML = rows
      .map((r) => {
        const pillCls =
          r.kindType === "strategy"
            ? " strategy"
            : r.kindType === "synth"
            ? " synth"
            : "";
        return `
          <tr>
            <td>
              <span class="cell-asset">
                <span class="cell-dot" style="background:${r.color}"></span>
                <span>${r.name}</span>
              </span>
            </td>
            <td><span class="kind-pill${pillCls}">${r.kindLabel}</span></td>
            <td class="muted-cell">${r.rangeText}</td>
            <td class="num">${formatMult(r.finalMult, 2)}</td>
            <td class="num strong-num">${formatPct(r.totalRet, 2)}</td>
            <td class="num strong-num">${formatPct(r.cagr, 2)}</td>
            <td class="num muted-cell">${formatPct(r.annVol, 2, false)}</td>
            <td class="num">${formatPct(r.maxDd, 2, false)}</td>
          </tr>
        `;
      })
      .join("");
  }

  function scheduleRender() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      currentModel = computeWindowModel();
      renderTopStatusAndLegend(currentModel);
      renderMainChart(currentModel);
      renderNavigator();
      renderSummaryTable(currentModel);
    });
  }

  function scheduleHoverOnly() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      if (!currentModel) currentModel = computeWindowModel();
      renderTopStatusAndLegend(currentModel);
      renderMainChart(currentModel);
      renderNavigator();
    });
  }

  // --- Interactive Events: Bottom Navigator Ruler ---
  function detectNavZone(x) {
    if (!navLayout) return null;
    const { selX1, selX2, padL, trackW } = navLayout;
    const handleRadius = 11;
    if (Math.abs(x - selX1) <= handleRadius) return "left";
    if (Math.abs(x - selX2) <= handleRadius) return "right";
    if (x > selX1 + handleRadius && x < selX2 - handleRadius) return "window";
    if (x >= padL && x <= padL + trackW) return "outside";
    return null;
  }

  function updateNavCursor(zone, dragging) {
    if (dragging) {
      if (zone === "left" || zone === "right") dom.navCanvas.style.cursor = "ew-resize";
      else if (zone === "window") dom.navCanvas.style.cursor = "grabbing";
      else dom.navCanvas.style.cursor = "crosshair";
      return;
    }
    if (zone === "left" || zone === "right") dom.navCanvas.style.cursor = "ew-resize";
    else if (zone === "window") dom.navCanvas.style.cursor = "grab";
    else dom.navCanvas.style.cursor = "crosshair";
  }

  dom.navCanvas.addEventListener("pointerdown", (e) => {
    if (!navLayout || e.button !== 0) return;
    const rect = dom.navCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const zone = detectNavZone(x) || "outside";

    dom.navCanvas.setPointerCapture(e.pointerId);
    state.navDrag = {
      pointerId: e.pointerId,
      mode: zone,
      startX: x,
      origStartIdx: state.winStartIdx,
      origEndIdx: state.winEndIdx,
      anchorIdx: navLayout.masterOfX(x),
      moved: false,
    };
    updateNavCursor(zone, true);
    scheduleHoverOnly();
  });

  dom.navCanvas.addEventListener("pointermove", (e) => {
    if (!navLayout) return;
    const rect = dom.navCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;

    if (!state.navDrag) {
      const z = detectNavZone(x);
      if (z !== state.navHoverZone) {
        state.navHoverZone = z;
        updateNavCursor(z, false);
        scheduleHoverOnly();
      }
      return;
    }

    const drag = state.navDrag;
    const dx = x - drag.startX;
    if (Math.abs(dx) > 3) drag.moved = true;

    const M = navLayout.M;
    const minSpan = Math.min(MIN_WINDOW_BARS, M - 1);
    const deltaBars = Math.round((dx / navLayout.trackW) * (M - 1));

    if (drag.mode === "left") {
      const nextStart = Math.max(0, Math.min(drag.origEndIdx - minSpan, drag.origStartIdx + deltaBars));
      setWindowByIndices(nextStart, drag.origEndIdx, true);
      scheduleRender();
    } else if (drag.mode === "right") {
      const nextEnd = Math.min(M - 1, Math.max(drag.origStartIdx + minSpan, drag.origEndIdx + deltaBars));
      setWindowByIndices(drag.origStartIdx, nextEnd, true);
      scheduleRender();
    } else if (drag.mode === "window") {
      const width = drag.origEndIdx - drag.origStartIdx;
      let nextStart = drag.origStartIdx + deltaBars;
      if (nextStart < 0) nextStart = 0;
      if (nextStart + width > M - 1) nextStart = M - 1 - width;
      setWindowByIndices(nextStart, nextStart + width, true);
      scheduleRender();
    } else if (drag.mode === "outside" && drag.moved) {
      const curIdx = navLayout.masterOfX(x);
      let s = Math.min(drag.anchorIdx, curIdx);
      let end = Math.max(drag.anchorIdx, curIdx);
      if (end - s < minSpan) {
        end = Math.min(M - 1, s + minSpan);
        s = Math.max(0, end - minSpan);
      }
      setWindowByIndices(s, end, true);
      scheduleRender();
    }
  });

  function finishNavDrag(e) {
    if (!state.navDrag) return;
    const drag = state.navDrag;
    if (drag.mode === "outside" && !drag.moved && navLayout) {
      // Single click on dimmed ruler centers current window around clicked date
      const width = drag.origEndIdx - drag.origStartIdx;
      let s = Math.round(drag.anchorIdx - width / 2);
      const M = navLayout.M;
      s = Math.max(0, Math.min(M - 1 - width, s));
      setWindowByIndices(s, s + width, true);
      scheduleRender();
    }
    state.navDrag = null;
    if (navLayout && e) {
      const rect = dom.navCanvas.getBoundingClientRect();
      state.navHoverZone = detectNavZone(e.clientX - rect.left);
      updateNavCursor(state.navHoverZone, false);
    }
    scheduleHoverOnly();
  }

  dom.navCanvas.addEventListener("pointerup", finishNavDrag);
  dom.navCanvas.addEventListener("pointercancel", finishNavDrag);
  dom.navCanvas.addEventListener("pointerleave", () => {
    if (!state.navDrag) {
      state.navHoverZone = null;
      scheduleHoverOnly();
    }
  });
  dom.navCanvas.addEventListener("dblclick", () => {
    applyPreset("全部", true);
  });

  // --- Interactive Events: Main Chart (Hover, Pan, X-Axis Drag Zoom, Wheel Zoom, Shift+Measure) ---
  dom.mainCanvas.addEventListener("pointerdown", (e) => {
    if (!mainLayout || e.button !== 0) return;
    const rect = dom.mainCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    if (x < mainLayout.padL || x > mainLayout.padL + mainLayout.plotW) return;

    dom.mainCanvas.setPointerCapture(e.pointerId);
    const clickedLocalIdx = mainLayout.idxOfX(x);
    const bottomPlotY = state.showDrawdown
      ? mainLayout.ddTop + mainLayout.ddH
      : mainLayout.mainTop + mainLayout.mainH;

    if (y >= bottomPlotY - 2) {
      // TradingView X-axis drag to zoom
      state.measureRange = null;
      state.mainDrag = {
        mode: "xaxis-zoom",
        startX: x,
        origStartIdx: state.winStartIdx,
        origEndIdx: state.winEndIdx,
        origSpan: state.winEndIdx - state.winStartIdx,
      };
      dom.mainCanvas.style.cursor = "ew-resize";
    } else if (e.shiftKey) {
      state.mainDrag = {
        mode: "measure",
        startIdx: clickedLocalIdx,
      };
      state.measureRange = { startIdx: clickedLocalIdx, endIdx: clickedLocalIdx };
      scheduleHoverOnly();
    } else {
      state.measureRange = null;
      state.mainDrag = {
        mode: "pan",
        startX: x,
        origStartIdx: state.winStartIdx,
        origEndIdx: state.winEndIdx,
        span: state.winEndIdx - state.winStartIdx,
      };
      dom.mainCanvas.style.cursor = "grabbing";
    }
  });

  dom.mainCanvas.addEventListener("pointermove", (e) => {
    if (!mainLayout) return;
    const rect = dom.mainCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (state.mainDrag) {
      if (state.mainDrag.mode === "xaxis-zoom") {
        const dx = x - state.mainDrag.startX;
        const M = state.masterDates.length;
        const minSpan = Math.min(10, M - 1);
        const factor = Math.exp(-dx * 0.005);
        const nextSpan = Math.max(minSpan, Math.min(M - 1, Math.round(state.mainDrag.origSpan * factor)));
        const nextStart = Math.max(0, Math.min(M - 1 - nextSpan, state.mainDrag.origEndIdx - nextSpan));
        state.hoverIdx = null;
        setWindowByIndices(nextStart, state.mainDrag.origEndIdx, true);
        scheduleRender();
      } else if (state.mainDrag.mode === "measure") {
        state.measureRange = {
          startIdx: state.mainDrag.startIdx,
          endIdx: mainLayout.idxOfX(x),
        };
        state.hoverIdx = null;
        scheduleHoverOnly();
      } else if (state.mainDrag.mode === "pan") {
        const dx = x - state.mainDrag.startX;
        const deltaBars = -Math.round((dx / mainLayout.plotW) * state.mainDrag.span);
        const M = state.masterDates.length;
        const w = state.mainDrag.span;
        let nextStart = state.mainDrag.origStartIdx + deltaBars;
        nextStart = Math.max(0, Math.min(M - 1 - w, nextStart));
        state.hoverIdx = null;
        setWindowByIndices(nextStart, nextStart + w, true);
        scheduleRender();
      }
      return;
    }

    const bottomPlotY = state.showDrawdown
      ? mainLayout.ddTop + mainLayout.ddH
      : mainLayout.mainTop + mainLayout.mainH;
    if (x >= mainLayout.padL && x <= mainLayout.padL + mainLayout.plotW && y >= bottomPlotY - 2) {
      dom.mainCanvas.style.cursor = "ew-resize";
    } else {
      dom.mainCanvas.style.cursor = "crosshair";
    }

    if (x >= mainLayout.padL && x <= mainLayout.padL + mainLayout.plotW && y < bottomPlotY) {
      const idx = mainLayout.idxOfX(x);
      if (idx !== state.hoverIdx) {
        state.hoverIdx = idx;
        scheduleHoverOnly();
      }
    } else if (state.hoverIdx !== null) {
      state.hoverIdx = null;
      scheduleHoverOnly();
    }
  });

  function finishMainDrag() {
    if (!state.mainDrag) return;
    state.mainDrag = null;
    dom.mainCanvas.style.cursor = "crosshair";
    scheduleHoverOnly();
  }

  dom.mainCanvas.addEventListener("pointerup", finishMainDrag);
  dom.mainCanvas.addEventListener("pointercancel", finishMainDrag);
  dom.mainCanvas.addEventListener("pointerleave", () => {
    if (!state.mainDrag && state.hoverIdx !== null) {
      state.hoverIdx = null;
      scheduleHoverOnly();
    }
  });

  dom.mainCanvas.addEventListener("dblclick", () => {
    state.measureRange = null;
    applyPreset("全部", true);
  });

  dom.mainCanvas.addEventListener(
    "wheel",
    (e) => {
      if (!mainLayout || state.masterDates.length < 4) return;
      const rect = dom.mainCanvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      if (x < mainLayout.padL || x > mainLayout.padL + mainLayout.plotW) return;

      e.preventDefault();
      const M = state.masterDates.length;
      const curSpan = state.winEndIdx - state.winStartIdx;
      const minSpan = Math.min(10, M - 1);

      // Horizontal trackpad two-finger pan
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && Math.abs(e.deltaX) > 2) {
        const panBars = Math.round((e.deltaX / mainLayout.plotW) * curSpan * 0.85);
        let nextStart = state.winStartIdx + panBars;
        nextStart = Math.max(0, Math.min(M - 1 - curSpan, nextStart));
        setWindowByIndices(nextStart, nextStart + curSpan, true);
        scheduleRender();
        return;
      }

      // Vertical wheel / pinch zoom anchored at cursor date
      const anchorRatio = Math.max(0, Math.min(1, (x - mainLayout.padL) / mainLayout.plotW));
      const anchorGlobalIdx = state.winStartIdx + anchorRatio * curSpan;
      const zoomFactor = Math.exp(e.deltaY * 0.0018);
      const nextSpan = Math.max(minSpan, Math.min(M - 1, Math.round(curSpan * zoomFactor)));

      let nextStart = Math.round(anchorGlobalIdx - anchorRatio * nextSpan);
      nextStart = Math.max(0, Math.min(M - 1 - nextSpan, nextStart));
      setWindowByIndices(nextStart, nextStart + nextSpan, true);
      scheduleRender();
    },
    { passive: false }
  );

  // --- Toolbar & Sidebar Controls Wiring ---
  dom.sidebarToggleBtn.addEventListener("click", () => {
    dom.appShell.classList.toggle("sidebar-collapsed");
    setTimeout(scheduleRender, 290);
  });

  dom.presetBar.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    applyPreset(btn.dataset.preset, true);
  });

  function onManualDateInput() {
    const sVal = dom.startDateInput.value;
    const eVal = dom.endDateInput.value;
    if (!sVal || !eVal) return;
    if (sVal >= eVal) {
      showToast("开始日期必须早于结束日期", true);
      syncDateInputs();
      return;
    }
    setWindowByDateStrings(sVal, eVal, true);
    scheduleRender();
  }

  dom.startDateInput.addEventListener("change", onManualDateInput);
  dom.endDateInput.addEventListener("change", onManualDateInput);
  document.querySelectorAll(".iso-date-field").forEach((lbl) => {
    lbl.addEventListener("click", () => {
      const inp = lbl.querySelector("input");
      if (inp && typeof inp.showPicker === "function") {
        try {
          inp.showPicker();
        } catch (_) {}
      }
    });
  });

  dom.scaleBar.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    state.scaleMode = btn.dataset.scale;
    dom.scaleBar.querySelectorAll(".seg-btn").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    scheduleRender();
  });

  dom.toggleEndLabels.addEventListener("click", () => {
    state.showEndLabels = !state.showEndLabels;
    dom.toggleEndLabels.classList.toggle("active", state.showEndLabels);
    scheduleRender();
  });

  dom.toggleDrawdown.addEventListener("click", () => {
    state.showDrawdown = !state.showDrawdown;
    dom.toggleDrawdown.classList.toggle("active", state.showDrawdown);
    scheduleRender();
  });

  dom.toggleRegimes.addEventListener("click", () => {
    state.showRegimes = !state.showRegimes;
    dom.toggleRegimes.classList.toggle("active", state.showRegimes);
    scheduleRender();
  });

  // Synthetic leverage controls
  dom.enableSynthToggle.addEventListener("change", () => {
    state.enableSynth = dom.enableSynthToggle.checked;
    dom.synthControls.classList.toggle("hidden", !state.enableSynth);
    rebuildMasterTimeline();
    scheduleRender();
  });

  dom.synthBaseSelect.addEventListener("change", () => {
    state.synthBase = dom.synthBaseSelect.value;
    rebuildMasterTimeline();
    scheduleRender();
  });

  function updateLeverage(val) {
    let parsed = parseFloat(val);
    if (Number.isNaN(parsed)) return;
    parsed = Math.max(-5.0, Math.min(10.0, Math.round(parsed * 100) / 100));
    state.leverage = parsed;
    dom.leverageInput.value = String(parsed);
    scheduleRender();
  }

  dom.leverageInput.addEventListener("input", () => {
    const v = parseFloat(dom.leverageInput.value);
    if (!Number.isNaN(v) && v >= -5 && v <= 10) {
      state.leverage = v;
      scheduleRender();
    }
  });
  dom.leverageInput.addEventListener("change", () => updateLeverage(dom.leverageInput.value));
  dom.levDecBtn.addEventListener("click", () => updateLeverage(state.leverage - 0.1));
  dom.levIncBtn.addEventListener("click", () => updateLeverage(state.leverage + 0.1));

  // Table header sorting
  dom.summaryTable.querySelector("thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    if (state.sortKey === key) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortKey = key;
      state.sortAsc = key === "name" || key === "kindLabel" || key === "rangeText";
    }
    if (currentModel) renderSummaryTable(currentModel);
  });

  // Add New Ticker Form
  dom.addTickerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = dom.newTickerInput.value.trim();
    if (!raw) return;

    dom.addTickerBtn.disabled = true;
    dom.addTickerBtn.textContent = "拉取中...";
    try {
      const resp = await fetch("/api/tickers/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || "拉取失败");
      }

      ingestCatalogPayload(data.catalog, false);
      if (data.ok && data.ok.length > 0) {
        for (const sym of data.ok) {
          if (state.catalogMap.has(sym) && !state.selectedKeys.includes(sym)) {
            state.selectedKeys.push(sym);
          }
        }
        dom.newTickerInput.value = "";
        showToast(`已拉取并加入：${data.ok.join(", ")}`);
      }
      if (data.failed && data.failed.length > 0) {
        const failTxt = data.failed.map((f) => `${f.symbol}(${f.error})`).join("；");
        showToast(`拉取失败：${failTxt}`, true);
      }
      rebuildMasterTimeline();
      renderSidebarCatalog();
      scheduleRender();
    } catch (err) {
      showToast(err.message || "网络或拉取异常", true);
    } finally {
      dom.addTickerBtn.disabled = false;
      dom.addTickerBtn.textContent = "加入";
    }
  });

  // Refresh Selected Market Tickers
  dom.refreshMarketBtn.addEventListener("click", async () => {
    const marketSelected = state.selectedKeys.filter(
      (k) => state.catalogMap.get(k)?.kind === "market"
    );
    if (marketSelected.length === 0) return;

    const full = dom.fullUpdateToggle.checked;
    dom.refreshMarketBtn.disabled = true;
    dom.refreshMarketBtn.textContent = "正在更新行情...";
    try {
      const resp = await fetch("/api/tickers/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: marketSelected, full }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || "更新失败");
      }

      ingestCatalogPayload(data.catalog, false);
      rebuildMasterTimeline();
      renderSidebarCatalog();
      scheduleRender();

      if (data.failed && data.failed.length > 0) {
        const failTxt = data.failed.map((f) => `${f.symbol}: ${f.error}`).join("；");
        showToast(`部分更新失败：${failTxt}`, true);
      } else {
        const modeTxt = data.mode === "full" ? "全量更新" : "增量更新";
        const detail = (data.updated || [])
          .map((u) => `${u.symbol}(+${u.addedRows})`)
          .join("、");
        showToast(`${modeTxt}完成：${detail}`);
      }
    } catch (err) {
      showToast(err.message || "更新异常", true);
    } finally {
      dom.refreshMarketBtn.disabled = false;
      dom.refreshMarketBtn.textContent = "更新已选行情";
    }
  });

  // ResizeObserver for crisp Retina responsive redraw
  const resizeObserver = new ResizeObserver(() => {
    scheduleRender();
  });
  resizeObserver.observe(dom.chartStage);
  resizeObserver.observe(dom.navigatorStage);

  // --- Bootstrap ---
  function ingestCatalogPayload(payload, setDefaults) {
    state.catalog = payload.items || [];
    state.parseErrors = payload.parseErrors || [];
    state.catalogMap = new Map(state.catalog.map((it) => [it.key, it]));
    assignCatalogColors();

    if (setDefaults) {
      const defaults = ["QQQ", "QLD", "TQQQ"].filter((k) => state.catalogMap.has(k));
      state.selectedKeys =
        defaults.length > 0
          ? defaults
          : state.catalog.length > 0
          ? [state.catalog[0].key]
          : [];
      if (state.catalogMap.has("QQQ")) state.synthBase = "QQQ";
      else if (state.catalog.length > 0) state.synthBase = state.catalog[0].key;
    } else {
      state.selectedKeys = state.selectedKeys.filter((k) => state.catalogMap.has(k));
    }
  }

  async function init() {
    if (window.innerWidth < 980) {
      dom.appShell.classList.add("sidebar-collapsed");
    }
    try {
      const resp = await fetch("/api/catalog");
      if (!resp.ok) throw new Error("无法加载数据目录");
      const payload = await resp.json();
      ingestCatalogPayload(payload, true);
      rebuildMasterTimeline();
      renderSidebarCatalog();
      scheduleRender();
    } catch (err) {
      dom.statusText.textContent = "数据加载失败";
      showToast(err.message || "初始化失败", true);
    }
  }

  init();
})();
