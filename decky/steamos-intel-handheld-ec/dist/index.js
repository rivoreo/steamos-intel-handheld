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
const applyPreset = callable("apply_limit");
// 100% is "no limit" from the user's point of view; calling it 100% invites the
// question of what the other numbers do to the battery.
const PRESETS = [60, 80, 100];
const COPY = {
    en: {
        pluginName: "Charge Limit",
        loading: "Reading the charge limit...",
        unavailable: "Charge limit is unavailable",
        errorPrefix: "Error",
        limitLabel: "Charge limit",
        headline: (stop) => `Charging stops at ${stop}%`,
        headlineUnlimited: "Charging to full",
        restartNote: (stop, restart) => `Charges back up once it falls below ${restart}%, so it settles between ${restart}% and ${stop}%.`,
        why: "Staying off a full charge slows battery ageing. Use a lower limit when the device mostly stays plugged in.",
        presetLabel: (limit) => `Stop at ${limit}%`,
        presetUnlimited: "Charge to full (100%)",
        applying: "Applying...",
        readOnlyWarning: "Read-only: the controller is not accepting writes right now.",
        detailsToggle: "Show technical details",
        detailsRegister: "Controller register",
        detailsValue: "Stored value",
        detailsWrites: "Writes",
        detailsWritable: "allowed",
        detailsReadOnly: "blocked",
        refresh: "Re-read from the controller",
    },
    zhHant: {
        pluginName: "充電上限",
        loading: "正在讀取充電上限...",
        unavailable: "無法讀取充電上限",
        errorPrefix: "錯誤",
        limitLabel: "充電上限",
        headline: (stop) => `充到 ${stop}% 就停`,
        headlineUnlimited: "充飽為止",
        restartNote: (stop, restart) => `掉到 ${restart}% 以下才會再充，所以電量會在 ${restart}% 到 ${stop}% 之間。`,
        why: "不充到滿可以減緩電池老化。經常插著電用的話，建議設低一點。",
        presetLabel: (limit) => `充到 ${limit}% 就停`,
        presetUnlimited: "充飽（100%）",
        applying: "套用中...",
        readOnlyWarning: "唯讀：控制器目前不接受寫入。",
        detailsToggle: "顯示技術細節",
        detailsRegister: "控制器暫存器",
        detailsValue: "儲存值",
        detailsWrites: "寫入",
        detailsWritable: "允許",
        detailsReadOnly: "阻擋",
        refresh: "重新讀取控制器",
    },
};
const blockStyle = {
    width: "100%",
    minWidth: 0,
    whiteSpace: "normal",
    overflowWrap: "break-word",
    lineHeight: 1.28,
};
const headlineStyle = {
    fontSize: "17px",
    fontWeight: 600,
    lineHeight: 1.25,
    whiteSpace: "normal",
    overflowWrap: "break-word",
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
    const [error, setError] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    const [showDetails, setShowDetails] = SP_REACT.useState(false);
    const loadStatus = async () => {
        setError(null);
        try {
            setStatus(await getStatus());
        }
        catch (err) {
            setStatus(null);
            setError(errorText(err));
        }
    };
    const applyLimit = async (limit) => {
        if (busy) {
            return;
        }
        setBusy(true);
        setError(null);
        try {
            const result = await applyPreset(limit);
            setStatus(result.applied);
        }
        catch (err) {
            setError(errorText(err));
        }
        finally {
            setBusy(false);
        }
    };
    SP_REACT.useEffect(() => {
        loadStatus();
    }, []);
    const stop = status?.end_threshold ?? null;
    const restart = status?.start_threshold ?? null;
    const unlimited = stop !== null && stop >= 100;
    const headline = !status
        ? error
            ? t.unavailable
            : t.loading
        : unlimited
            ? t.headlineUnlimited
            : t.headline(stop);
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsx("div", { style: headlineStyle, children: headline }), status && !unlimited && restart !== null ? (SP_JSX.jsx("div", { style: detailStyle, children: t.restartNote(stop, restart) })) : null, status && !status.writes_enabled ? (SP_JSX.jsx("div", { style: detailStyle, children: t.readOnlyWarning })) : null, error ? (SP_JSX.jsxs("div", { role: "alert", style: detailStyle, children: [t.errorPrefix, ": ", error] })) : null] }) }) }), SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: t.limitLabel, description: t.why, 
                        // Inline puts the value in a narrow column beside the label, where
                        // anything longer than a couple of characters is cut off mid-word.
                        layout: "below", rgOptions: PRESETS.map((limit) => ({
                            data: String(limit),
                            label: limit >= 100 ? t.presetUnlimited : t.presetLabel(limit),
                        })), selectedOption: stop === null ? "" : String(stop), disabled: busy || !status, onChange: (option) => applyLimit(Number(option.data)) }) }) }), SP_JSX.jsxs(DFL.PanelSection, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: t.detailsToggle, checked: showDetails, onChange: setShowDetails }) }), showDetails && status ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [SP_JSX.jsxs("div", { style: detailStyle, children: [t.detailsRegister, ": ", status.address_hex] }), SP_JSX.jsxs("div", { style: detailStyle, children: [t.detailsValue, ": ", status.raw_hex] }), SP_JSX.jsxs("div", { style: detailStyle, children: [t.detailsWrites, ":", " ", status.writes_enabled ? t.detailsWritable : t.detailsReadOnly] })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: loadStatus, children: busy ? t.applying : t.refresh }) })] })) : null] })] }));
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
