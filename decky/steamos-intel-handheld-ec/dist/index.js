const manifest = {"name":"Charge Limit"};
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
function FaBatteryHalf (props) {
  return GenIcon({"attr":{"viewBox":"0 0 640 512"},"child":[{"tag":"path","attr":{"d":"M544 160v64h32v64h-32v64H64V160h480m16-64H48c-26.51 0-48 21.49-48 48v224c0 26.51 21.49 48 48 48h512c26.51 0 48-21.49 48-48v-16h8c13.255 0 24-10.745 24-24V184c0-13.255-10.745-24-24-24h-8v-16c0-26.51-21.49-48-48-48zm-240 96H96v128h224V192z"},"child":[]}]})(props);
}

const getStatus = callable("get_status");
callable("preview_limit");
const applyPreset = callable("apply_limit");
const PRESETS = [
    { value: 60, label: "60%" },
    { value: 80, label: "80%" },
    { value: 100, label: "100%" },
];
const COPY = {
    en: {
        pluginName: "Charge Limit",
        panelTitle: "Battery Charge Limit",
        loadingTitle: "Reading charge limit",
        loadingMessage: "Checking the MSI charge-limit byte...",
        unavailableTitle: "Unable to read charge limit",
        unavailableBody: "The Decky backend did not return a status.",
        errorPrefix: "Error",
        refresh: "Refresh",
        rawValue: "EC byte",
        rule: "Rule",
        writeMode: "Mode",
        readOnly: "Read-only",
        writeEnabled: "Writable",
        writeNotice: "Writes are enabled for validated 60/80/100% presets.",
        setPreset: (limit) => `Set ${limit}%`,
        previewing: (limit) => `Previewing ${limit}% preset...`,
        previewReady: (limit, rawHex) => `${limit}% preview: ${rawHex}. No EC write was sent.`,
        applying: (limit) => `Setting ${limit}%...`,
        appliedReady: (limit, rawHex) => `Set ${limit}% (${rawHex}).`,
        stopRestart: (stop, restart) => `${stop}% stop / ${restart}% restart`,
        restartRule: (stop, restart) => `Stops at ${stop}% and restarts below ${restart}%.`,
    },
    zhHant: {
        pluginName: "充電上限",
        panelTitle: "電池充電上限",
        loadingTitle: "正在讀取充電上限",
        loadingMessage: "正在檢查 MSI 充電限制 EC 位元...",
        unavailableTitle: "無法讀取充電上限",
        unavailableBody: "Decky 後端沒有回傳狀態。",
        errorPrefix: "錯誤",
        refresh: "重新讀取",
        rawValue: "EC 位元",
        rule: "規則",
        writeMode: "模式",
        readOnly: "唯讀",
        writeEnabled: "可寫",
        writeNotice: "已驗證 60/80/100% 設定，現在可以寫入。",
        setPreset: (limit) => `設為 ${limit}%`,
        previewing: (limit) => `正在預覽 ${limit}% 設定...`,
        previewReady: (limit, rawHex) => `${limit}% 預覽值：${rawHex}。沒有寫入 EC。`,
        applying: (limit) => `正在設為 ${limit}%...`,
        appliedReady: (limit, rawHex) => `已設為 ${limit}%（${rawHex}）。`,
        stopRestart: (stop, restart) => `${stop}% 停止 / ${restart}% 重新充電`,
        restartRule: (stop, restart) => `充到 ${stop}% 停止，低於 ${restart}% 才重新充電。`,
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
    opacity: 0.72,
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
const PluginTitle = () => {
    const t = COPY[useLocale()];
    return SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: t.pluginName });
};
const EcChargePanel = () => {
    const t = COPY[useLocale()];
    const [status, setStatus] = SP_REACT.useState(null);
    const [notice, setNotice] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState(null);
    const loadStatus = async () => {
        setNotice(null);
        setError(null);
        try {
            setStatus(await getStatus());
            setNotice(t.writeNotice);
        }
        catch (error) {
            setStatus(null);
            setError(errorText(error));
        }
    };
    const applyLimit = async (limit) => {
        setNotice(t.applying(limit));
        setError(null);
        try {
            const result = await applyPreset(limit);
            setStatus(result.applied);
            setNotice(t.appliedReady(limit, result.applied.raw_hex));
        }
        catch (error) {
            setError(errorText(error));
            setNotice(null);
        }
    };
    SP_REACT.useEffect(() => {
        loadStatus();
    }, []);
    return (SP_JSX.jsxs(DFL.PanelSection, { title: t.panelTitle, children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { className: DFL.staticClasses.Title, style: titleStyle, children: status
                                ? t.stopRestart(status.end_threshold, status.start_threshold)
                                : error
                                    ? t.unavailableTitle
                                    : t.loadingTitle }), status ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: [t.rawValue, ": ", status.address_hex, " = ", status.raw_hex] }), SP_JSX.jsxs("div", { children: [t.rule, ": ", t.restartRule(status.end_threshold, status.start_threshold)] }), SP_JSX.jsxs("div", { children: [t.writeMode, ": ", status.writes_enabled ? t.writeEnabled : t.readOnly] })] })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx("div", { children: error ? t.unavailableBody : t.loadingMessage }), error ? (SP_JSX.jsxs("div", { style: detailStyle, children: [t.errorPrefix, ": ", error] })) : null] }))] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: loadStatus, children: t.refresh }) }), PRESETS.map((preset) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => applyLimit(preset.value), children: t.setPreset(preset.value) }) }, preset.value))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: notice ?? (status ? t.writeNotice : t.loadingMessage) }) })] }));
};
var index = definePlugin(() => ({
    name: "Charge Limit",
    titleView: SP_JSX.jsx(PluginTitle, {}),
    content: SP_JSX.jsx(EcChargePanel, {}),
    icon: SP_JSX.jsx(FaBatteryHalf, {}),
    onDismount() { },
}));

export { index as default };
//# sourceMappingURL=index.js.map
