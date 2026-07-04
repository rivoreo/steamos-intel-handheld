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
    var {
        attr,
        size,
        title
      } = props,
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
const restoreDefaults = callable("restore_defaults");
const COPY = {
    en: {
        pluginName: "Game Power",
        panelTitle: "Game Power",
        loading: "Reading game-power status...",
        unavailable: "Game-power status is unavailable.",
        currentMode: "Current mode",
        serviceState: "Background service",
        sample: "Live status",
        action: "Action",
        game: "Current game",
        package: "Package",
        core: "CPU",
        graphics: "GPU side",
        noSample: "No foreground game sample",
        refresh: "Refresh",
        restore: "Use service default",
        applying: "Applying...",
        restored: "Using the service default.",
        modes: {
            automatic: "Balance CPU/GPU",
            observe: "View data only",
            off: "Turn scheduler off",
            default: "Service default",
            unknown: "Unknown",
        },
        modeDescriptions: {
            automatic: "Balances CPU and GPU power while a game is running.",
            observe: "Keeps sampling and decisions visible without changing power settings.",
            off: "Stops game-power sampling and leaves power behavior to the system.",
            default: "Uses the packaged default power policy.",
            unknown: "The active game-power mode could not be identified.",
        },
        actions: {
            "observe-only": "Viewing data only",
            "gpu-priority-epp": "GPU priority",
            "gpu-priority-cpu-cap": "GPU priority with CPU cap",
            "off": "Scheduler off",
        },
        reasons: {
            "mode is observe": "Data-only mode is active; no power settings are changed.",
            "mode is off": "The scheduler is off; sampling and power changes are stopped.",
            "package limited with GPU activity": "GPU activity is high, so power is being held for graphics.",
            "package limited with high core pressure": "CPU pressure is high, so CPU power is capped to protect GPU power.",
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
        sample: "即時狀態",
        action: "動作",
        game: "目前遊戲",
        package: "封包",
        core: "CPU",
        graphics: "GPU 側",
        noSample: "目前沒有前景遊戲樣本",
        refresh: "重新讀取",
        restore: "使用服務預設",
        applying: "正在套用...",
        restored: "已切回服務預設。",
        modes: {
            automatic: "平衡 CPU/GPU",
            observe: "只看數據",
            off: "完全停用",
            default: "服務預設",
            unknown: "未知",
        },
        modeDescriptions: {
            automatic: "遊戲執行時自動平衡 CPU 與 GPU 功耗。",
            observe: "保留採樣與判斷，只顯示數據，不改變功耗設定。",
            off: "停止遊戲電力採樣與調度，交回系統處理。",
            default: "使用套件內建的預設電力策略。",
            unknown: "無法辨識目前的遊戲電力模式。",
        },
        actions: {
            "observe-only": "只看數據",
            "gpu-priority-epp": "GPU 優先",
            "gpu-priority-cpu-cap": "GPU 優先，限制 CPU 搶功耗",
            "off": "已完全停用",
        },
        reasons: {
            "mode is observe": "目前只看數據，不會改動功耗設定。",
            "mode is off": "目前已完全停用，停止採樣與調度。",
            "package limited with GPU activity": "GPU 負載偏高，正在把功耗留給顯示核心。",
            "package limited with high core pressure": "CPU 壓力偏高，正在限制 CPU 搶功耗。",
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
    return value === null ? "-" : `${value.toFixed(1)} W`;
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
function modeLabel(t, mode) {
    return t.modes[modeKey(mode)] ?? t.modes.unknown;
}
function modeDescription(t, mode) {
    return t.modeDescriptions[modeKey(mode)] ?? t.modeDescriptions.unknown;
}
const PluginTitle = () => {
    const t = COPY[useLocale()];
    return SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: t.pluginName });
};
const GamePowerPanel = () => {
    const t = COPY[useLocale()];
    const [status, setStatus] = SP_REACT.useState(null);
    const [sample, setSample] = SP_REACT.useState(null);
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
            setSample(await sampleOnce());
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
            setSample(await sampleOnce());
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
    SP_REACT.useEffect(() => {
        load();
    }, []);
    const sampleTitle = sample?.appid ? `${t.game}: ${sample.appid}` : t.noSample;
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs(DFL.PanelSection, { title: t.panelTitle, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: status
                                        ? `${t.currentMode}: ${modeLabel(t, status.mode)}`
                                        : busy
                                            ? t.loading
                                            : t.unavailable }), status ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.serviceState, ": ", status.active_state, "/", status.sub_state] }), SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.policyLabels, status.policy_label) }), SP_JSX.jsx("div", { style: detailStyle, children: modeDescription(t, status.mode) })] })) : null, notice ? SP_JSX.jsx("div", { style: detailStyle, children: notice }) : null, error ? (SP_JSX.jsxs("div", { role: "alert", style: detailStyle, children: [t.errorPrefix, ": ", error] })) : null] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("automatic"), children: busy ? t.applying : t.modes.automatic }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("observe"), children: busy ? t.applying : t.modes.observe }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyMode("off"), children: busy ? t.applying : t.modes.off }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: t.sample, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: sampleTitle }), sample ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.action, ": ", mappedText(t.actions, sample.action)] }), SP_JSX.jsxs("div", { children: [t.package, ": ", fmtWatts(sample.package_w)] }), SP_JSX.jsxs("div", { children: [t.core, ": ", fmtWatts(sample.core_w)] }), SP_JSX.jsxs("div", { children: [t.graphics, ": ", fmtWatts(sample.uncore_w)] }), sample.reason ? (SP_JSX.jsx("div", { style: detailStyle, children: mappedText(t.reasons, sample.reason) })) : null] })) : (SP_JSX.jsx("div", { style: detailStyle, children: busy ? t.loading : t.noSample }))] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: load, children: busy ? t.applying : t.refresh }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: restore, children: busy ? t.applying : t.restore }) })] })] }));
};
var index = definePlugin(() => ({
    name: "Game Power",
    titleView: SP_JSX.jsx(PluginTitle, {}),
    content: SP_JSX.jsx(GamePowerPanel, {}),
    icon: SP_JSX.jsx(FaGamepad, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
