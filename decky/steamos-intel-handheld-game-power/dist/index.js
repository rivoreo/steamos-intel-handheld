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

const getStatus = callable("get_status");
const sampleOnce = callable("sample_once");
const setMode = callable("set_mode");
const setFpsTarget = callable("set_fps_target");
const restoreDefaults = callable("restore_defaults");
const COPY = {
    en: {
        pluginName: "Game Power",
        panelTitle: "Game Power",
        loading: "Reading game-power status...",
        unavailable: "Game-power status is unavailable.",
        currentMode: "Current mode",
        serviceState: "Background service",
        telemetry: "Runtime telemetry",
        control: "Control",
        fpsTarget: "FPS target",
        targetAuto: "Use SteamOS limit",
        targetManual: "Manual FPS target",
        targetApply: "Set FPS target",
        learning: "Learning status",
        learningBeforeReuse: "Learning before reuse",
        learningReady: "Can reuse next launch",
        learningNeedsTarget: "Needs stable FPS target",
        learningStopped: "Sampling is stopped",
        manualProbe: "Manual sample",
        probeNotice: "Probe sample - not daemon control",
        action: "Action",
        game: "Current game",
        package: "Package",
        core: "CPU",
        graphics: "GPU side",
        noSample: "No foreground game sample",
        refresh: "Refresh",
        readProbe: "Read one sample",
        restore: "Use service default",
        applying: "Applying...",
        restored: "Using the service default.",
        evidenceLabel: "Local evidence",
        evidenceStates: {
            "target-aware-live": "Local target/frame evidence ready",
            "power-signals-only": "Local evidence: power signals only",
            "view-data-only": "View data only",
            stopped: "Game Power stopped",
            "control-invalid": "Local evidence unavailable",
            unavailable: "Local evidence unavailable",
        },
        modes: {
            automatic: "Balance to FPS target",
            observe: "Watch data only",
            off: "Stop Game Power",
            default: "Service default",
            unknown: "Unknown",
        },
        modeDescriptions: {
            automatic: "Adjusts CPU/GPU shared power while the game is below its FPS target.",
            observe: "Shows live samples without changing power.",
            off: "Sampling is stopped",
            default: "Uses the packaged default power policy.",
            unknown: "The active game-power mode could not be identified.",
        },
        telemetryLabels: {
            targetAware: "Target-aware balancing",
            powerSignals: "Learning before reuse",
            collecting: "Learning before reuse",
            stale: "Runtime data is stale",
            unavailable: "Daemon runtime data is unavailable",
        },
        targetStates: {
            known: "FPS target known",
            unknown: "FPS target unknown",
            unlimited: "FPS target unlimited",
            unsupported: "FPS target unsupported",
        },
        frameStates: {
            live: "Frame data live",
            missing: "Frame data missing",
            stale: "Frame data stale",
            malformed: "Frame data malformed",
            unsupported: "Frame data unsupported",
        },
        actions: {
            "observe-only": "Viewing data only",
            "gpu-priority-epp": "GPU priority",
            "gpu-priority-cpu-cap": "GPU priority with CPU cap",
            idle: "Idle",
            restore: "Restoring system policy",
        },
        reasons: {
            "mode is observe": "Data-only mode is active; no power settings are changed.",
            "mode is off": "Game Power is stopped; sampling and power changes are stopped.",
            "package limited with GPU activity": "GPU activity is high, so power is being held for graphics.",
            "package limited with high core pressure": "CPU pressure is high, so CPU power is capped to protect GPU power.",
        },
        classifications: {
            "control-disabled": "Scheduler off",
            "observe-only": "Watch data only",
            "no-foreground-game": "No foreground game sample",
            "fps-target-satisfied": "FPS target already satisfied",
            "insufficient-power-evidence": "Learning before reuse",
            "not-package-bound": "Package power is not the limit",
            "gpu-package-bound": "GPU-side package pressure detected",
            "gpu-package-bound-cpu-contention": "CPU/GPU power contention detected",
        },
        policyLabels: {
            "Balanced automatic policy": "Game balance policy",
        },
        errorPrefix: "Error",
    },
    zhHant: {
        pluginName: "遊戲電力",
        panelTitle: "遊戲電力",
        loading: "正在讀取遊戲電力狀態...",
        unavailable: "無法讀取遊戲電力狀態。",
        currentMode: "目前模式",
        serviceState: "背景服務",
        telemetry: "執行中資料",
        control: "控制",
        fpsTarget: "FPS 目標",
        targetAuto: "使用 SteamOS 限制",
        targetManual: "手動 FPS 目標",
        targetApply: "設定 FPS 目標",
        learning: "學習狀態",
        learningBeforeReuse: "學習中，暫不復用",
        learningReady: "下次可直接套用",
        learningNeedsTarget: "需要穩定 FPS 目標",
        learningStopped: "已停止採樣",
        manualProbe: "手動採樣",
        probeNotice: "手動採樣 - 不代表 daemon 正在控制",
        action: "動作",
        game: "目前遊戲",
        package: "封包",
        core: "CPU",
        graphics: "GPU 側",
        noSample: "目前沒有前景遊戲樣本",
        refresh: "重新讀取",
        readProbe: "讀取一次樣本",
        restore: "使用服務預設",
        applying: "正在套用...",
        restored: "已切回服務預設。",
        evidenceLabel: "本機證據",
        evidenceStates: {
            "target-aware-live": "本機 FPS 目標與影格資料可用",
            "power-signals-only": "本機證據：僅有功耗訊號",
            "view-data-only": "只看數據",
            stopped: "遊戲電力已停止",
            "control-invalid": "本機證據不可用",
            unavailable: "本機證據不可用",
        },
        modes: {
            automatic: "依 FPS 目標自動平衡",
            observe: "只看數據，不調整功耗",
            off: "停止遊戲電力",
            default: "服務預設",
            unknown: "未知",
        },
        modeDescriptions: {
            automatic: "低於 FPS 目標時，才會調整 CPU/GPU 的共用功耗。",
            observe: "只顯示即時採樣，不改動功耗。",
            off: "已停止採樣",
            default: "使用套件內建的預設電力策略。",
            unknown: "無法辨識目前的遊戲電力模式。",
        },
        telemetryLabels: {
            targetAware: "依 FPS 目標平衡",
            powerSignals: "學習中，暫不復用",
            collecting: "學習中，暫不復用",
            stale: "執行中資料已過期",
            unavailable: "缺少 daemon 執行中資料",
        },
        targetStates: {
            known: "FPS 目標已知",
            unknown: "FPS 目標未知",
            unlimited: "FPS 目標未限制",
            unsupported: "FPS 目標不支援",
        },
        frameStates: {
            live: "影格資料即時可用",
            missing: "缺少影格資料",
            stale: "影格資料已過期",
            malformed: "影格資料格式異常",
            unsupported: "影格資料不支援",
        },
        actions: {
            "observe-only": "只看數據",
            "gpu-priority-epp": "GPU 優先",
            "gpu-priority-cpu-cap": "GPU 優先，限制 CPU 搶功耗",
            idle: "閒置",
            restore: "正在還原系統策略",
        },
        reasons: {
            "mode is observe": "目前只看數據，不會改動功耗設定。",
            "mode is off": "目前已停止遊戲電力，停止採樣與功耗調整。",
            "package limited with GPU activity": "GPU 負載偏高，正在把功耗留給顯示核心。",
            "package limited with high core pressure": "CPU 壓力偏高，正在限制 CPU 搶功耗。",
        },
        classifications: {
            "control-disabled": "已停止採樣",
            "observe-only": "只看數據，不調整功耗",
            "no-foreground-game": "目前沒有前景遊戲樣本",
            "fps-target-satisfied": "FPS 目標已達成",
            "insufficient-power-evidence": "學習中，暫不復用",
            "not-package-bound": "封包功耗尚未成為限制",
            "gpu-package-bound": "偵測到 GPU 側封包功耗壓力",
            "gpu-package-bound-cpu-contention": "偵測到 CPU/GPU 搶功耗",
        },
        policyLabels: {
            "Balanced automatic policy": "遊戲平衡策略",
        },
        errorPrefix: "錯誤",
    },
};
const blockStyle = {
    width: "100%",
    minWidth: 0,
    whiteSpace: "normal",
    overflowWrap: "break-word",
    lineHeight: 1.28,
};
const titleStyle = {
    marginBottom: "8px",
};
const detailStyle = {
    opacity: 0.74,
    fontSize: "13px",
    marginTop: "4px",
    whiteSpace: "normal",
    overflowWrap: "anywhere",
};
const sliderStyle = {
    width: "100%",
    marginTop: "8px",
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
function fmtPercent(value) {
    return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}
function mappedText(map, value) {
    if (!value) {
        return "-";
    }
    return map[value] ?? value;
}
function modeKey(mode) {
    if (mode === "automatic" || mode === "observe" || mode === "off" || mode === "default") {
        return mode;
    }
    return "unknown";
}
function isTargetAwareReady(readiness) {
    return readiness?.status === "target-aware-live" && readiness?.claim_ready === true;
}
function evidenceText(t, readiness) {
    if (!readiness) {
        return t.evidenceStates.unavailable;
    }
    if (!isTargetAwareReady(readiness) && readiness.status === "target-aware-live") {
        return t.evidenceStates.unavailable;
    }
    return t.evidenceStates[readiness.status] ?? t.evidenceStates.unavailable;
}
function modeLabel(t, mode, runtime) {
    if (mode === "automatic" && isTargetAwareReady(runtime?.evidence_readiness)) {
        return t.telemetryLabels.targetAware;
    }
    return t.modes[modeKey(mode)] ?? t.modes.unknown;
}
function modeDescription(t, mode) {
    return t.modeDescriptions[modeKey(mode)] ?? t.modeDescriptions.unknown;
}
function targetText(t, target) {
    if (!target) {
        return t.targetStates.unknown;
    }
    const label = mappedText(t.targetStates, target.status);
    return target.fps ? `${label}: ${target.fps.toFixed(0)} FPS` : label;
}
function frameText(t, frame) {
    if (!frame) {
        return t.frameStates.missing;
    }
    const label = mappedText(t.frameStates, frame.status);
    return frame.avg_fps ? `${label}: ${frame.avg_fps.toFixed(1)} FPS` : label;
}
function learningText(t, learning) {
    if (!learning) {
        return t.learningBeforeReuse;
    }
    if (learning.reusable_next_launch) {
        return t.learningReady;
    }
    if (learning.skip_reason === "fps_target_unknown" || learning.status === "waiting-for-fps-target") {
        return t.learningNeedsTarget;
    }
    if (learning.status === "stopped" || learning.status === "view-data-only") {
        return t.learningStopped;
    }
    const samples = learning.session_samples ?? 0;
    const required = learning.required_samples ?? 0;
    return required > 0
        ? `${t.learningBeforeReuse}: ${samples}/${required}`
        : t.learningBeforeReuse;
}
function runtimeHeadline(t, mode, runtime) {
    if (mode === "off") {
        return t.modeDescriptions.off;
    }
    if (mode === "observe") {
        return t.modeDescriptions.observe;
    }
    if (!runtime || runtime.error) {
        return t.telemetryLabels.unavailable;
    }
    if (runtime.stale) {
        return t.telemetryLabels.stale;
    }
    if (isTargetAwareReady(runtime?.evidence_readiness)) {
        return t.telemetryLabels.targetAware;
    }
    if (runtime.frame_source.status !== "live") {
        return t.telemetryLabels.collecting;
    }
    return t.telemetryLabels.powerSignals;
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
    const [sample, setSample] = SP_REACT.useState(null);
    const [manualFps, setManualFps] = SP_REACT.useState(40);
    const [notice, setNotice] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    const load = async () => {
        setBusy(true);
        setNotice(null);
        setError(null);
        try {
            const statusResult = await getStatus();
            setStatus(statusResult.service);
            setControl(statusResult.control);
            setRuntime(statusResult.runtime);
            if (statusResult.control.fps_target_override.fps) {
                setManualFps(statusResult.control.fps_target_override.fps);
            }
            else if (statusResult.runtime.fps_target.fps) {
                setManualFps(statusResult.runtime.fps_target.fps);
            }
        }
        catch (error) {
            setError(errorText(error));
        }
        finally {
            setBusy(false);
        }
    };
    const readProbe = async () => {
        if (busy) {
            return;
        }
        setBusy(true);
        setNotice(null);
        setError(null);
        try {
            setSample(await sampleOnce());
        }
        catch (error) {
            setError(errorText(error));
        }
        finally {
            setBusy(false);
        }
    };
    const applyMode = async (mode) => {
        if (busy) {
            return;
        }
        setBusy(true);
        setNotice(t.applying);
        setError(null);
        try {
            await setMode(mode);
            const statusResult = await getStatus();
            setStatus(statusResult.service);
            setControl(statusResult.control);
            setRuntime(statusResult.runtime);
            setNotice(null);
        }
        catch (error) {
            setError(errorText(error));
            setNotice(null);
        }
        finally {
            setBusy(false);
        }
    };
    const restore = async () => {
        if (busy) {
            return;
        }
        setBusy(true);
        setNotice(t.applying);
        setError(null);
        try {
            await restoreDefaults();
            const statusResult = await getStatus();
            setStatus(statusResult.service);
            setControl(statusResult.control);
            setRuntime(statusResult.runtime);
            setNotice(t.restored);
        }
        catch (error) {
            setError(errorText(error));
            setNotice(null);
        }
        finally {
            setBusy(false);
        }
    };
    const applyFpsTarget = async (fps) => {
        if (busy) {
            return;
        }
        setBusy(true);
        setNotice(t.applying);
        setError(null);
        try {
            await setFpsTarget(fps);
            const statusResult = await getStatus();
            setStatus(statusResult.service);
            setControl(statusResult.control);
            setRuntime(statusResult.runtime);
            if (statusResult.control.fps_target_override.fps) {
                setManualFps(statusResult.control.fps_target_override.fps);
            }
            setNotice(null);
        }
        catch (error) {
            setError(errorText(error));
            setNotice(null);
        }
        finally {
            setBusy(false);
        }
    };
    SP_REACT.useEffect(() => {
        load();
    }, []);
    const runtimeTitle = runtime?.appid ? `${t.game}: ${runtime.appid}` : t.noSample;
    const probeTitle = sample?.appid ? `${t.game}: ${sample.appid}` : t.noSample;
    const supportedMin = control?.fps_target_override.supported_min ?? 30;
    const supportedMax = control?.fps_target_override.supported_max ?? 120;
    const supportedStep = control?.fps_target_override.supported_step ?? 5;
    const targetMode = control?.fps_target_override.status === "manual"
        ? `${t.targetManual}: ${control.fps_target_override.fps} FPS`
        : t.targetAuto;
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSection, { title: t.panelTitle, children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: status
                                    ? `${t.currentMode}: ${modeLabel(t, status.mode, runtime)}`
                                    : busy
                                        ? t.loading
                                        : t.unavailable }), status ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.serviceState, ": ", status.active_state, "/", status.sub_state] }), SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.policyLabels, status.policy_label) }), SP_JSX.jsx("div", { style: detailStyle, children: modeDescription(t, status.mode) }), SP_JSX.jsx("div", { style: detailStyle, children: runtimeHeadline(t, status.mode, runtime) }), SP_JSX.jsxs("div", { style: detailStyle, children: [t.evidenceLabel, ": ", evidenceText(t, runtime?.evidence_readiness)] }), SP_JSX.jsx("div", { style: detailStyle, children: targetText(t, runtime?.fps_target) }), SP_JSX.jsx("div", { style: detailStyle, children: frameText(t, runtime?.frame_source) }), SP_JSX.jsxs("div", { style: detailStyle, children: [t.learning, ": ", learningText(t, runtime?.learning)] })] })) : null, notice ? SP_JSX.jsx("div", { style: detailStyle, children: notice }) : null, error ? (SP_JSX.jsxs("div", { role: "alert", style: detailStyle, children: [t.errorPrefix, ": ", error] })) : null] }) }) }), SP_JSX.jsxs(DFL.PanelSection, { title: t.control, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("automatic"), children: busy ? t.applying : t.modes.automatic }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("observe"), children: busy ? t.applying : t.modes.observe }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("off"), children: busy ? t.applying : t.modes.off }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsxs("div", { className: DFL.staticClasses.Title, style: titleStyle, children: [t.fpsTarget, ": ", manualFps, " FPS"] }), SP_JSX.jsx("div", { style: detailStyle, children: targetMode }), SP_JSX.jsxs("div", { style: detailStyle, children: [supportedMin, "-", supportedMax, " FPS / ", supportedStep] }), SP_JSX.jsx("input", { "aria-label": t.targetManual, type: "range", min: 30, max: 120, step: 5, value: manualFps, style: sliderStyle, onChange: (event) => setManualFps(Number(event.currentTarget.value)) })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyFpsTarget(manualFps), children: busy ? t.applying : t.targetApply }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyFpsTarget(null), children: busy ? t.applying : t.targetAuto }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: t.telemetry, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: runtimeTitle }), runtime ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.action, ": ", mappedText(t.actions, runtime.last_action)] }), SP_JSX.jsxs("div", { children: [t.package, ": ", fmtWatts(runtime.package_w)] }), SP_JSX.jsxs("div", { children: [t.core, ": ", fmtWatts(runtime.core_w)] }), SP_JSX.jsxs("div", { children: [t.graphics, ": ", fmtWatts(runtime.uncore_w)] }), SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.classifications, runtime.classification_primary) }), SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.reasons, runtime.last_reason) }), SP_JSX.jsxs("div", { style: detailStyle, children: ["Render: ", fmtPercent(runtime.render_busy)] }), SP_JSX.jsxs("div", { style: detailStyle, children: [t.learning, ": ", learningText(t, runtime.learning)] })] })) : (SP_JSX.jsx("div", { style: detailStyle, children: busy ? t.loading : t.noSample }))] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: load, children: busy ? t.applying : t.refresh }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: restore, children: busy ? t.applying : t.restore }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: t.manualProbe, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: probeTitle }), SP_JSX.jsx("div", { style: detailStyle, children: t.probeNotice }), sample ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.action, ": ", mappedText(t.actions, sample.action)] }), SP_JSX.jsxs("div", { children: [t.package, ": ", fmtWatts(sample.package_w)] }), SP_JSX.jsxs("div", { children: [t.core, ": ", fmtWatts(sample.core_w)] }), SP_JSX.jsxs("div", { children: [t.graphics, ": ", fmtWatts(sample.uncore_w)] }), SP_JSX.jsx("div", { style: detailStyle, children: targetText(t, sample.fps_target) }), SP_JSX.jsx("div", { style: detailStyle, children: frameText(t, sample.frame_source) }), sample.reason ? (SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.reasons, sample.reason) })) : null] })) : null] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: readProbe, children: busy ? t.applying : t.readProbe }) })] })] }));
};
var index = definePlugin(() => ({
    name: "Game Power",
    titleView: SP_JSX.jsx(PluginTitle, {}),
    content: SP_JSX.jsx(GamePowerPanel, {}),
    icon: SP_JSX.jsx(FaGamepad, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
