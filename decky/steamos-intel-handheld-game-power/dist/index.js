const manifest = {"name":"Game Power"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;
const definePlugin = (fn) => {
    return (...args) => {
        return fn(...args);
    };
};

var DefaultContext = {
  color: undefined,
  size: undefined,
  className: undefined,
  style: undefined,
  attr: undefined
};
var IconContext = SP_REACT.createContext && /*#__PURE__*/SP_REACT.createContext(DefaultContext);

var _excluded = ["attr", "size", "title"];
function _objectWithoutProperties(e, t) { if (null == e) return {}; var o, r, i = _objectWithoutPropertiesLoose(e, t); if (Object.getOwnPropertySymbols) { var n = Object.getOwnPropertySymbols(e); for (r = 0; r < n.length; r++) o = n[r], -1 === t.indexOf(o) && {}.propertyIsEnumerable.call(e, o) && (i[o] = e[o]); } return i; }
function _objectWithoutPropertiesLoose(r, e) { if (null == r) return {}; var t = {}; for (var n in r) if ({}.hasOwnProperty.call(r, n)) { if (-1 !== e.indexOf(n)) continue; t[n] = r[n]; } return t; }
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function ownKeys(e, r) { var t = Object.keys(e); if (Object.getOwnPropertySymbols) { var o = Object.getOwnPropertySymbols(e); r && (o = o.filter(function (r) { return Object.getOwnPropertyDescriptor(e, r).enumerable; })), t.push.apply(t, o); } return t; }
function _objectSpread(e) { for (var r = 1; r < arguments.length; r++) { var t = null != arguments[r] ? arguments[r] : {}; r % 2 ? ownKeys(Object(t), true).forEach(function (r) { _defineProperty(e, r, t[r]); }) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys(Object(t)).forEach(function (r) { Object.defineProperty(e, r, Object.getOwnPropertyDescriptor(t, r)); }); } return e; }
function _defineProperty(e, r, t) { return (r = _toPropertyKey(r)) in e ? Object.defineProperty(e, r, { value: t, enumerable: true, configurable: true, writable: true }) : e[r] = t, e; }
function _toPropertyKey(t) { var i = _toPrimitive(t, "string"); return "symbol" == typeof i ? i : i + ""; }
function _toPrimitive(t, r) { if ("object" != typeof t || !t) return t; var e = t[Symbol.toPrimitive]; if (void 0 !== e) { var i = e.call(t, r); if ("object" != typeof i) return i; throw new TypeError("@@toPrimitive must return a primitive value."); } return ("string" === r ? String : Number)(t); }
function Tree2Element(tree) {
  return tree && tree.map((node, i) => /*#__PURE__*/SP_REACT.createElement(node.tag, _objectSpread({
    key: i
  }, node.attr), Tree2Element(node.child)));
}
function GenIcon(data) {
  return props => /*#__PURE__*/SP_REACT.createElement(IconBase, _extends({
    attr: _objectSpread({}, data.attr)
  }, props), Tree2Element(data.child));
}
function IconBase(props) {
  var elem = conf => {
    var attr = props.attr,
      size = props.size,
      title = props.title,
      svgProps = _objectWithoutProperties(props, _excluded);
    var computedSize = size || conf.size || "1em";
    var className;
    if (conf.className) className = conf.className;
    if (props.className) className = (className ? className + " " : "") + props.className;
    return /*#__PURE__*/SP_REACT.createElement("svg", _extends({
      stroke: "currentColor",
      fill: "currentColor",
      strokeWidth: "0"
    }, conf.attr, attr, svgProps, {
      className: className,
      style: _objectSpread(_objectSpread({
        color: props.color || conf.color
      }, conf.style), props.style),
      height: computedSize,
      width: computedSize,
      xmlns: "http://www.w3.org/2000/svg"
    }), title && /*#__PURE__*/SP_REACT.createElement("title", null, title), props.children);
  };
  return IconContext !== undefined ? /*#__PURE__*/SP_REACT.createElement(IconContext.Consumer, null, conf => elem(conf)) : elem(DefaultContext);
}

// THIS FILE IS AUTO GENERATED
function FaGamepad (props) {
  return GenIcon({"attr":{"viewBox":"0 0 640 512"},"child":[{"tag":"path","attr":{"d":"M480.07 96H160a160 160 0 1 0 114.24 272h91.52A160 160 0 1 0 480.07 96zM248 268a12 12 0 0 1-12 12h-52v52a12 12 0 0 1-12 12h-24a12 12 0 0 1-12-12v-52H84a12 12 0 0 1-12-12v-24a12 12 0 0 1 12-12h52v-52a12 12 0 0 1 12-12h24a12 12 0 0 1 12 12v52h52a12 12 0 0 1 12 12zm216 76a40 40 0 1 1 40-40 40 40 0 0 1-40 40zm64-96a40 40 0 1 1 40-40 40 40 0 0 1-40 40z"},"child":[]}]})(props);
}

const PROFILE_ORDER = [
    "auto",
    "battery",
    "quiet",
    "performance",
    "observe",
    "off",
];
const PROFILE_TO_PERSONA = {
    battery: "battery",
    quiet: "ac-quiet",
    performance: "ac-performance",
};
const getStatus = callable("get_status");
callable("sample_once");
const setMode = callable("set_mode");
const setFpsTarget = callable("set_fps_target");
const restoreDefaults = callable("restore_defaults");
const setPersona = callable("set_persona");
const clearPersona = callable("clear_persona");
callable("limiter_status");
callable("set_limiter");
callable("clear_limiter");
const COPY = {
    en: {
        pluginName: "Game Power",
        panelTitle: "Game Power",
        loading: "Checking...",
        unavailable: "Game Power is unavailable",
        status: {
            off: "Turned off",
            observe: "Watching only",
            noGame: "Waiting for a game",
            noTarget: "No frame rate target set",
            stale: "Reconnecting...",
            starting: "Warming up...",
            loadingScene: "Loading - full power",
            boosting: "Full power",
            holding: "Holding steady",
            holdingSaving: "Holding steady, using less power",
        },
        holdingWithTarget: (state, fps) => `${state} at ${fps} FPS`,
        nowLine: (fps, watts) => `${fps} FPS now, ${watts}`,
        profileLabel: "Power profile",
        profileDescription: "How much power to spend holding your frame rate.",
        profiles: {
            auto: "Automatic",
            battery: "Save battery",
            quiet: "Quiet",
            performance: "Performance",
            observe: "Watch only",
            off: "Off",
        },
        profileHints: {
            auto: "Follows whether you are on battery or plugged in.",
            battery: "Lowest power that still holds your frame rate.",
            quiet: "Keeps power down so fans stay quiet.",
            performance: "Spends full power for the most headroom.",
            observe: "Collects data without changing anything.",
            off: "Game Power makes no changes at all.",
        },
        targetLabel: "Frame rate target",
        targetAuto: "Automatic",
        targetAutoDescription: () => "Follows the SteamOS limit and lowers it if a scene cannot hold it.",
        targetAutoDetected: (fps) => `Automatic (now ${fps} FPS)`,
        targetFixed: (fps) => `${fps} FPS`,
        targetManualDescription: (fps) => `Set by you: ${fps} FPS.`,
        unreachableTitle: "Target looks out of reach",
        unreachableBody: (target, actual) => `This scene is running near ${actual} FPS at full power, below your ${target} FPS target.`,
        unreachableAction: (fps) => `Set the target to ${fps} FPS`,
        applying: "Applying...",
        restored: "Restored",
        errorPrefix: "Error",
        diagnosticsToggle: "Show technical details",
        diagnosticsDescription: "Live scheduler internals. Not needed for normal use.",
        diag: {
            doing: "Doing",
            activityNothing: "Nothing - the game is within its target",
            activityTrimming: "Lowering power while holding the target",
            activityFullPower: "Full power - the target is not being met",
            activityLoading: "Full power - the game is loading",
            activityCapped: (fps) => `Holding a ${fps} FPS cap`,
            activityNoGame: "Nothing - no game is running",
            power: "Power draw",
            core: "CPU",
            graphics: "graphics",
            slowestFrames: "Slowest frames",
            slowestFramesValue: (ms, fps) => `${ms} (about ${fps} FPS)`,
            gpuLoad: "Graphics load",
            service: "Background service",
            serviceOk: "Running normally",
            serviceProblem: (raw) => `Not running normally (${raw})`,
            learning: "Learning",
            restore: "Restore defaults",
            refresh: "Refresh now",
        },
        phases: {
            "no-game": "No game",
            "no-target": "No target",
            loading: "Loading",
            "at-target": "At target",
            "above-target": "Above target",
            "below-target-cpu-bound": "Below target (CPU)",
            "below-target-gpu-bound": "Below target (graphics)",
            unknown: "Unknown",
        },
        actions: {
            idle: "Idle",
            "observe-only": "Observing",
            restore: "Restored",
            "gpu-priority-epp": "Graphics priority",
            "target-balance-trim": "Trimming",
            "target-balance-release": "Released",
            "loading-boost": "Loading boost",
        },
        actuatorStates: { active: "active", blocked: "blocked", pending: "pending" },
        laneNames: {
            foreground: "Foreground",
            background: "Background",
            ladder: "Deep steps",
            other: "Other",
        },
        laneStates: { active: "active", blocked: "blocked", released: "released" },
        verdictStates: {
            ready: "ready",
            unavailable: "unavailable",
            invalid: "invalid",
            missing: "missing",
        },
        evidenceStates: {
            "target-aware-live": "Target and frame data ready",
            "power-signals-only": "Power signals only",
            unavailable: "Unavailable",
            stopped: "Stopped",
            "view-data-only": "View data only",
        },
        frameStates: {
            live: "Live",
            missing: "Missing",
            stale: "Stale",
            "profiler-only": "Profiler only",
        },
        targetStates: {
            known: "Known",
            unknown: "Unknown",
            unlimited: "Unlimited",
            "none-configured": "Not configured",
        },
        limiterStates: {
            unknown: "Unknown",
            unsupported: "Not supported",
            applied: "Applied",
            cleared: "Cleared",
            ready: "Ready",
        },
        learningStates: {
            ready: "Can reuse next launch",
            learning: "Learning before reuse",
            needsTarget: "Needs a steady target",
            stopped: "Stopped",
        },
        none: "None",
    },
    zhHant: {
        pluginName: "遊戲電力",
        panelTitle: "遊戲電力",
        loading: "檢查中...",
        unavailable: "遊戲電力無法使用",
        status: {
            off: "已關閉",
            observe: "只觀察",
            noGame: "等待遊戲中",
            noTarget: "尚未設定張數目標",
            stale: "重新連線中...",
            starting: "暖機中...",
            loadingScene: "載入中 - 全力輸出",
            boosting: "全力輸出",
            holding: "穩定維持中",
            holdingSaving: "穩定維持中，功耗已降低",
        },
        holdingWithTarget: (state, fps) => `${fps} FPS ${state}`,
        nowLine: (fps, watts) => `目前 ${fps} FPS，${watts}`,
        profileLabel: "電力模式",
        profileDescription: "決定要花多少功耗來維持你的張數。",
        profiles: {
            auto: "自動",
            battery: "省電",
            quiet: "安靜",
            performance: "效能",
            observe: "只觀察",
            off: "關閉",
        },
        profileHints: {
            auto: "依照使用電池或插電自動切換。",
            battery: "在維持張數的前提下用最低功耗。",
            quiet: "壓低功耗，讓風扇保持安靜。",
            performance: "全力輸出，保留最多餘裕。",
            observe: "只收集資料，不做任何調整。",
            off: "遊戲電力完全不介入。",
        },
        targetLabel: "張數目標",
        targetAuto: "自動",
        targetAutoDescription: () => "跟隨 SteamOS 的限制；場景撐不住時會自動調低。",
        targetAutoDetected: (fps) => `自動（目前 ${fps} FPS）`,
        targetFixed: (fps) => `${fps} FPS`,
        targetManualDescription: (fps) => `你設定的目標：${fps} FPS。`,
        unreachableTitle: "目標似乎達不到",
        unreachableBody: (target, actual) => `這個場景在全力輸出下大約只有 ${actual} FPS，低於你設定的 ${target} FPS。`,
        unreachableAction: (fps) => `把目標改成 ${fps} FPS`,
        applying: "套用中...",
        restored: "已還原",
        errorPrefix: "錯誤",
        diagnosticsToggle: "顯示技術細節",
        diagnosticsDescription: "調度器的即時內部狀態，一般使用不需要看。",
        diag: {
            doing: "正在做什麼",
            activityNothing: "沒有介入 —— 遊戲在目標之內",
            activityTrimming: "維持目標的同時降低功耗",
            activityFullPower: "全力輸出 —— 目標未達成",
            activityLoading: "全力輸出 —— 遊戲載入中",
            activityCapped: (fps) => `限制在 ${fps} FPS`,
            activityNoGame: "沒有介入 —— 目前沒有遊戲在跑",
            power: "功耗",
            core: "處理器",
            graphics: "繪圖",
            slowestFrames: "最慢的那些影格",
            slowestFramesValue: (ms, fps) => `${ms}（約 ${fps} FPS）`,
            gpuLoad: "繪圖負載",
            service: "背景服務",
            serviceOk: "正常運作中",
            serviceProblem: (raw) => `沒有正常運作（${raw}）`,
            learning: "學習",
            restore: "還原預設",
            refresh: "立即重新整理",
        },
        phases: {
            "no-game": "沒有遊戲",
            "no-target": "沒有目標",
            loading: "載入中",
            "at-target": "已達目標",
            "above-target": "超過目標",
            "below-target-cpu-bound": "未達目標（處理器）",
            "below-target-gpu-bound": "未達目標（繪圖）",
            unknown: "未知",
        },
        actions: {
            idle: "閒置",
            "observe-only": "觀察中",
            restore: "已還原",
            "gpu-priority-epp": "繪圖優先",
            "target-balance-trim": "調整中",
            "target-balance-release": "已放開",
            "loading-boost": "載入增壓",
        },
        actuatorStates: { active: "作用中", blocked: "阻擋", pending: "等待中" },
        laneNames: {
            foreground: "前景",
            background: "背景",
            ladder: "深層階梯",
            other: "其他",
        },
        laneStates: { active: "作用中", blocked: "阻擋", released: "已釋放" },
        verdictStates: {
            ready: "可用",
            unavailable: "不可用",
            invalid: "無效",
            missing: "不存在",
        },
        evidenceStates: {
            "target-aware-live": "目標與影格資料可用",
            "power-signals-only": "僅有功耗訊號",
            unavailable: "不可用",
            stopped: "已停止",
            "view-data-only": "只看數據",
        },
        frameStates: {
            live: "即時",
            missing: "缺少",
            stale: "過期",
            "profiler-only": "僅分析工具",
        },
        targetStates: {
            known: "已知",
            unknown: "未知",
            unlimited: "無限制",
            "none-configured": "未設定",
        },
        limiterStates: {
            unknown: "未知",
            unsupported: "不支援",
            applied: "已套用",
            cleared: "已清除",
            ready: "就緒",
        },
        learningStates: {
            ready: "下次可直接套用",
            learning: "學習中，暫不復用",
            needsTarget: "需要穩定的目標",
            stopped: "已停止",
        },
        none: "無",
    },
};
const headlineStyle = {
    fontSize: "17px",
    fontWeight: 600,
    lineHeight: 1.25,
    whiteSpace: "normal",
    overflowWrap: "break-word",
};
const nowStyle = {
    opacity: 0.75,
    fontSize: "13px",
    marginTop: "4px",
};
const blockStyle = {
    width: "100%",
    minWidth: 0,
    whiteSpace: "normal",
    overflowWrap: "break-word",
    lineHeight: 1.28,
};
const detailStyle = {
    opacity: 0.74,
    fontSize: "12px",
    marginTop: "3px",
    whiteSpace: "normal",
    overflowWrap: "anywhere",
};
const noticeStyle = {
    ...blockStyle,
    fontSize: "13px",
    marginTop: "2px",
};
function localeFromLanguage(language) {
    const value = (language ?? "").toLowerCase().replace("_", "-");
    if (value.includes("tchinese") ||
        value.includes("traditional") ||
        value.includes("zh-tw") ||
        value.includes("zh-hant") ||
        value.includes("zh-hk") ||
        value.includes("zh-mo")) {
        return "zhHant";
    }
    return "en";
}
function initialLocale() {
    const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
    return localeFromLanguage(languages.find(Boolean));
}
function useLocale() {
    const [locale, setLocale] = SP_REACT.useState(initialLocale);
    SP_REACT.useEffect(() => {
        let mounted = true;
        window.SteamClient?.Settings?.GetCurrentLanguage?.()
            .then((language) => {
            if (mounted) {
                setLocale(localeFromLanguage(language));
            }
        })
            .catch(() => { });
        return () => {
            mounted = false;
        };
    }, []);
    return locale;
}
function errorText(error) {
    return error instanceof Error ? error.message : String(error);
}
function fmtWatts(value) {
    return value === null || value === undefined ? "-" : `${value.toFixed(1)} W`;
}
function fmtMs(value) {
    return value === null || value === undefined ? "-" : `${value.toFixed(1)} ms`;
}
function fmtPercent(value) {
    return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}
function mappedText(map, value) {
    if (!value) {
        return "-";
    }
    return map[value] ?? value;
}
function isBelowTarget(phase) {
    return phase === "below-target-cpu-bound" || phase === "below-target-gpu-bound";
}
function activeProfile(control) {
    if (!control) {
        return "auto";
    }
    if (control.mode === "off") {
        return "off";
    }
    if (control.mode === "observe") {
        return "observe";
    }
    const override = control.persona_override;
    if (override?.status === "manual" && override.persona) {
        if (override.persona === "battery") {
            return "battery";
        }
        if (override.persona === "ac-quiet") {
            return "quiet";
        }
        if (override.persona === "ac-performance") {
            return "performance";
        }
    }
    return "auto";
}
/** Plain-language answer to "what is it doing right now". */
function headlineText(t, control, runtime) {
    if (!control) {
        return t.loading;
    }
    if (control.mode === "off") {
        return t.status.off;
    }
    if (control.mode === "observe") {
        return t.status.observe;
    }
    if (!runtime || runtime.error) {
        return t.unavailable;
    }
    if (runtime.stale) {
        return t.status.stale;
    }
    if (!runtime.appid) {
        return t.status.noGame;
    }
    const target = runtime.fps_target.fps;
    if (!target) {
        return t.status.noTarget;
    }
    const phase = runtime.phase;
    if (phase === "loading") {
        return t.status.loadingScene;
    }
    if (isBelowTarget(phase)) {
        return t.status.boosting;
    }
    if (phase === "at-target" || phase === "above-target") {
        const trimmed = (runtime.trim_rungs_active?.length ?? 0) > 0;
        return trimmed ? t.status.holdingSaving : t.status.holding;
    }
    return t.status.starting;
}
/**
 * Sustained "below target even with nothing trimmed" means the target itself is
 * the problem, not the scheduler. Suggest a reachable one rather than silently
 * burning full power forever.
 */
function unreachableSuggestion(avgFps, step) {
    const floor = Math.floor(avgFps / step) * step;
    return Math.max(step, floor);
}
const StatusCard = ({ t, control, runtime, notice, error }) => {
    const target = runtime?.fps_target.fps ?? null;
    const headline = headlineText(t, control, runtime);
    const showNow = control?.mode !== "off" && runtime && !runtime.error && !runtime.stale && runtime.appid;
    const avg = runtime?.frame_source.avg_fps ?? null;
    return (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { style: headlineStyle, children: target && (headline === t.status.holding || headline === t.status.holdingSaving)
                        ? t.holdingWithTarget(headline, target.toFixed(0))
                        : headline }), showNow ? (SP_JSX.jsx("div", { style: nowStyle, children: t.nowLine(avg === null ? "-" : avg.toFixed(0), fmtWatts(runtime?.package_w)) })) : null, notice ? SP_JSX.jsx("div", { style: noticeStyle, children: notice }) : null, error ? (SP_JSX.jsxs("div", { role: "alert", style: noticeStyle, children: [t.errorPrefix, ": ", error] })) : null] }) }));
};
const Diagnostics = ({ t, status, runtime }) => {
    if (!runtime) {
        return null;
    }
    const rows = [];
    // Deliberately not shown: ladder step, rung ids, thread-group tallies, gated
    // lanes, verdict ledger. They are scheduler bookkeeping with no meaning to
    // anyone who is not us, and the runtime snapshot carries them for debugging.
    rows.push([t.diag.doing, describeActivity(t, runtime)]);
    rows.push([
        t.diag.power,
        `${fmtWatts(runtime.package_w)} (${t.diag.core} ${fmtWatts(runtime.core_w)} / ${t.diag.graphics} ${fmtWatts(runtime.uncore_w)})`,
    ]);
    // Frame timing only means anything while a game is drawing. With the machine
    // sitting at the library the compositor idles, which drags the pacing budget
    // up to values like 6463 ms - a real number that reads as a broken one.
    const p95 = runtime.appid ? runtime.frame_source.p95_ms : null;
    if (p95) {
        rows.push([
            t.diag.slowestFrames,
            t.diag.slowestFramesValue(fmtMs(p95), String(Math.round(1000 / p95))),
        ]);
    }
    const gpu = runtime.auto_target?.gpu;
    if (gpu && gpu.render_busy !== null && gpu.render_busy !== undefined) {
        rows.push([t.diag.gpuLoad, fmtPercent(gpu.render_busy)]);
    }
    if (status) {
        rows.push([t.diag.service, describeService(t, status)]);
    }
    return (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: rows.map(([label, value]) => (SP_JSX.jsxs("div", { style: detailStyle, children: [label, ": ", value] }, label))) }) }));
};
/**
 * systemd's own vocabulary ("active/running") is not something a Steam Deck
 * user should have to learn. Say it plainly, and keep the raw pair only when
 * something is wrong, where it is the thing worth pasting into a bug report.
 */
function describeService(t, status) {
    const raw = `${status.active_state}/${status.sub_state}`;
    if (status.active_state === "active" && status.sub_state === "running") {
        return t.diag.serviceOk;
    }
    return t.diag.serviceProblem(raw);
}
function describeActivity(t, runtime) {
    const trims = runtime.trim_rungs_active?.length ?? 0;
    const cap = runtime.auto_target?.cap_applied_fps ?? null;
    if (!runtime.appid) {
        return t.diag.activityNoGame;
    }
    if (cap) {
        return t.diag.activityCapped(String(cap));
    }
    if (runtime.phase === "loading") {
        return t.diag.activityLoading;
    }
    if (isBelowTarget(runtime.phase)) {
        return t.diag.activityFullPower;
    }
    return trims > 0 ? t.diag.activityTrimming : t.diag.activityNothing;
}
const PluginTitle = () => {
    const t = COPY[useLocale()];
    return SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: t.pluginName });
};
const GamePowerPanel = () => {
    const t = COPY[useLocale()];
    const [status, setStatus] = SP_REACT.useState(null);
    const [control, setControl] = SP_REACT.useState(null);
    const [runtime, setRuntime] = SP_REACT.useState(null);
    SP_REACT.useState(null);
    // null == "follow whatever the daemon detected". Never seed a literal here:
    // a hardcoded default renders as a real FPS target the user never chose, and
    // one tap on Set/Apply would commit it.
    const [manualFps, setManualFps] = SP_REACT.useState(null);
    SP_REACT.useState(null);
    const [showDiagnostics, setShowDiagnostics] = SP_REACT.useState(false);
    const [notice, setNotice] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    // Consecutive polls spent below target with nothing of ours applied.
    const [starvedPolls, setStarvedPolls] = SP_REACT.useState(0);
    const busyRef = SP_REACT.useRef(busy);
    busyRef.current = busy;
    const absorb = (result) => {
        setStatus(result.service);
        setControl(result.control);
        setRuntime(result.runtime);
        const detected = result.control.fps_target_override.fps ?? result.runtime.fps_target.fps;
        if (detected) {
            // Only seed the slider; never clobber a value the user is dragging.
            setManualFps((current) => current ?? detected);
        }
        // "Full power and still short" only counts when we are not the cause.
        const starved = isBelowTarget(result.runtime.phase) &&
            (result.runtime.trim_rungs_active?.length ?? 0) === 0 &&
            !!result.runtime.fps_target.fps;
        setStarvedPolls((current) => (starved ? current + 1 : 0));
    };
    const load = async () => {
        setBusy(true);
        setNotice(null);
        setError(null);
        try {
            absorb(await getStatus());
        }
        catch (err) {
            setError(errorText(err));
        }
        finally {
            setBusy(false);
        }
    };
    SP_REACT.useEffect(() => {
        load();
    }, []);
    // The panel is the only place the live governor is observable, so keep it
    // ticking instead of showing whatever was true when it was opened.
    SP_REACT.useEffect(() => {
        let cancelled = false;
        const timer = setInterval(async () => {
            if (busyRef.current) {
                return;
            }
            try {
                const result = await getStatus();
                if (!cancelled) {
                    absorb(result);
                }
            }
            catch {
                // Transient backend hiccup; the next tick retries.
            }
        }, 2000);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, []);
    const guard = async (work) => {
        if (busy) {
            return;
        }
        setBusy(true);
        setNotice(t.applying);
        setError(null);
        try {
            await work();
            setNotice(null);
        }
        catch (err) {
            setError(errorText(err));
            setNotice(null);
        }
        finally {
            setBusy(false);
        }
    };
    const applyProfile = (profile) => guard(async () => {
        if (profile === "off" || profile === "observe") {
            await setMode(profile === "off" ? "off" : "observe");
        }
        else {
            await setMode("automatic");
            const persona = PROFILE_TO_PERSONA[profile];
            if (persona) {
                await setPersona(persona);
            }
            else {
                await clearPersona();
            }
        }
        absorb(await getStatus());
    });
    const applyFpsTarget = (fps) => guard(async () => {
        await setFpsTarget(fps);
        // Clearing the override hands the slider back to auto-detect, so drop the
        // local value instead of pinning the old one.
        if (fps === null) {
            setManualFps(null);
        }
        setStarvedPolls(0);
        absorb(await getStatus());
    });
    const restore = () => guard(async () => {
        await restoreDefaults();
        absorb(await getStatus());
        setNotice(t.restored);
    });
    const supportedMin = control?.fps_target_override.supported_min ?? 30;
    const supportedMax = control?.fps_target_override.supported_max ?? 120;
    const supportedStep = control?.fps_target_override.supported_step ?? 5;
    const detectedFps = control?.fps_target_override.fps ?? runtime?.fps_target.fps ?? null;
    const manualTarget = control?.fps_target_override.status === "manual";
    // Offer only rates the panel can actually pace evenly: exact divisors of the
    // current refresh rate, as computed by the daemon. There is no working VRR
    // here, so an off-divisor target judders no matter how well we schedule it.
    const reported = (runtime?.auto_target?.candidates ?? []).filter((fps) => fps >= supportedMin && fps <= supportedMax);
    // Never let a missing field kill the control. If the daemon has not reported
    // its divisor list, fall back to the standard divisors of a 120 Hz panel plus
    // whatever target is currently detected, so the user can still choose one.
    // A greyed-out dropdown with no explanation is the worst possible outcome.
    const fallback = [120, 60, 40, 30].filter((fps) => fps >= supportedMin && fps <= supportedMax);
    const candidates = reported.length
        ? reported
        : Array.from(new Set([...(detectedFps ? [Math.round(detectedFps)] : []), ...fallback])).sort((a, b) => b - a);
    // "auto" is a real choice, not the absence of one.
    const targetOptions = [
        { data: "auto", label: manualTarget ? t.targetAuto : t.targetAutoDetected(detectedFps === null ? "-" : detectedFps.toFixed(0)) },
        ...candidates.map((fps) => ({ data: String(fps), label: t.targetFixed(String(fps)) })),
    ];
    const selectedTarget = manualTarget ? String(control?.fps_target_override.fps ?? "") : "auto";
    const profile = activeProfile(control);
    const targetFps = runtime?.fps_target.fps ?? null;
    const avgFps = runtime?.frame_source.avg_fps ?? null;
    // 15 polls at 2 s == ~30 s of sustained shortfall before offering advice.
    const showUnreachable = starvedPolls >= 15 && targetFps !== null && avgFps !== null && avgFps < targetFps;
    const suggestedFps = avgFps === null ? null : unreachableSuggestion(avgFps, supportedStep);
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(StatusCard, { t: t, control: control, runtime: runtime, notice: notice, error: error }) }), showUnreachable && suggestedFps !== null && targetFps !== null ? (SP_JSX.jsxs(DFL.PanelSection, { title: t.unreachableTitle, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: SP_JSX.jsx("div", { style: detailStyle, children: t.unreachableBody(targetFps.toFixed(0), avgFps.toFixed(0)) }) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyFpsTarget(suggestedFps), children: busy ? t.applying : t.unreachableAction(suggestedFps.toFixed(0)) }) })] })) : null, SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: t.profileLabel, description: t.profileHints[profile], 
                        // Inline puts the value in a narrow column beside the label, where
                        // anything longer than a couple of characters is cut off mid-word.
                        layout: "below", rgOptions: PROFILE_ORDER.map((key) => ({ data: key, label: t.profiles[key] })), selectedOption: profile, disabled: busy, onChange: (option) => applyProfile(option.data) }) }) }), profile === "off" ? null : (SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: t.targetLabel, description: manualTarget
                            ? t.targetManualDescription(String(control?.fps_target_override.fps ?? ""))
                            : t.targetAutoDescription(), layout: "below", rgOptions: targetOptions, selectedOption: selectedTarget, disabled: busy, onChange: (option) => applyFpsTarget(option.data === "auto" ? null : Number(option.data)) }) }) })), SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: t.diagnosticsToggle, description: showDiagnostics ? t.diagnosticsDescription : undefined, checked: showDiagnostics, onChange: setShowDiagnostics }) }) }), showDiagnostics ? (SP_JSX.jsx(SP_JSX.Fragment, { children: SP_JSX.jsxs(DFL.PanelSection, { title: t.diagnosticsToggle, children: [SP_JSX.jsx(Diagnostics, { t: t, status: status, runtime: runtime }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: SP_JSX.jsxs("div", { style: detailStyle, children: [t.diag.learning, ": ", mappedText(t.learningStates, learningKey(runtime?.learning))] }) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: load, children: busy ? t.applying : t.diag.refresh }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: restore, children: busy ? t.applying : t.diag.restore }) })] }) })) : null] }));
};
function learningKey(learning) {
    if (!learning) {
        return "learning";
    }
    if (learning.reusable_next_launch) {
        return "ready";
    }
    if (learning.skip_reason === "fps_target_unknown" ||
        learning.status === "waiting-for-fps-target") {
        return "needsTarget";
    }
    if (learning.status === "stopped" || learning.status === "view-data-only") {
        return "stopped";
    }
    return "learning";
}
var index = definePlugin(() => ({
    name: "Game Power",
    titleView: SP_JSX.jsx(PluginTitle, {}),
    content: SP_JSX.jsx(GamePowerPanel, {}),
    icon: SP_JSX.jsx(FaGamepad, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
