import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { FC, useEffect, useState } from "react";
import { FaGamepad } from "react-icons/fa";

type ServiceStatus = {
  active_state: string;
  sub_state: string;
  mode: string;
  override_active: boolean;
  policy_label: string;
};

type StatusPayload = {
  service: ServiceStatus;
};

type SamplePayload = {
  appid: string | null;
  action: string | null;
  reason: string | null;
  package_w: number | null;
  core_w: number | null;
  uncore_w: number | null;
  pl1_w: number | null;
  render_busy: number | null;
};

type Mode = "automatic" | "observe" | "off";

const getStatus = callable<[], StatusPayload>("get_status");
const sampleOnce = callable<[], SamplePayload>("sample_once");
const setMode = callable<[mode: Mode], { mode: Mode; policy_label: string }>("set_mode");
const restoreDefaults = callable<[], { restored: boolean; policy_label: string }>(
  "restore_defaults",
);

type LocaleKey = "en" | "zhHant";

type Copy = {
  pluginName: string;
  panelTitle: string;
  loading: string;
  unavailable: string;
  currentMode: string;
  serviceState: string;
  sample: string;
  action: string;
  game: string;
  package: string;
  core: string;
  graphics: string;
  noSample: string;
  refresh: string;
  restore: string;
  applying: string;
  restored: string;
  modes: Record<string, string>;
  modeDescriptions: Record<string, string>;
  actions: Record<string, string>;
  reasons: Record<string, string>;
  policyLabels: Record<string, string>;
  errorPrefix: string;
};

const COPY: Record<LocaleKey, Copy> = {
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
      automatic: "Balanced power",
      observe: "Monitor only",
      off: "Power scheduler off",
      default: "Service default",
      unknown: "Unknown",
    },
    modeDescriptions: {
      automatic: "Balances CPU and GPU power while a game is running.",
      observe: "Reads game-power data without changing power behavior.",
      off: "Leaves CPU and GPU power behavior to the system.",
      default: "Uses the packaged default power policy.",
      unknown: "The active game-power mode could not be identified.",
    },
    actions: {
      "observe-only": "Monitor only",
      "gpu-priority-epp": "GPU priority",
      "gpu-priority-cpu-cap": "GPU priority with CPU cap",
      "off": "Power scheduler off",
    },
    reasons: {
      "mode is observe": "Monitor-only mode is active.",
      "mode is off": "The power scheduler is off.",
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
      automatic: "平衡調度",
      observe: "只監測",
      off: "停用調度",
      default: "服務預設",
      unknown: "未知",
    },
    modeDescriptions: {
      automatic: "遊戲執行時會平衡 CPU 與 GPU 的功耗。",
      observe: "只讀取遊戲電力資料，不改變功耗行為。",
      off: "不接管 CPU/GPU 功耗，交回系統處理。",
      default: "使用套件內建的預設電力策略。",
      unknown: "無法辨識目前的遊戲電力模式。",
    },
    actions: {
      "observe-only": "只監測",
      "gpu-priority-epp": "GPU 優先",
      "gpu-priority-cpu-cap": "GPU 優先，限制 CPU 搶功耗",
      "off": "停用調度",
    },
    reasons: {
      "mode is observe": "目前是只監測模式，不會改動功耗。",
      "mode is off": "目前已停用電力調度。",
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
} as const;

const titleStyle = {
  marginBottom: "8px",
} as const;

const detailStyle = {
  opacity: 0.74,
  fontSize: "13px",
  marginTop: "4px",
  whiteSpace: "normal",
  overflowWrap: "anywhere",
} as const;

function localeFromLanguage(language: string | undefined): LocaleKey {
  const value = (language ?? "").toLowerCase().replace("_", "-");
  if (
    value.includes("tchinese") ||
    value.includes("traditional") ||
    value.includes("zh-tw") ||
    value.includes("zh-hant") ||
    value.includes("zh-hk") ||
    value.includes("zh-mo")
  ) {
    return "zhHant";
  }
  return "en";
}

function initialLocale(): LocaleKey {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  return localeFromLanguage(languages.find(Boolean));
}

function useLocale(): LocaleKey {
  const [locale, setLocale] = useState<LocaleKey>(initialLocale);

  useEffect(() => {
    let mounted = true;
    window.SteamClient?.Settings?.GetCurrentLanguage?.()
      .then((language: string) => {
        if (mounted) {
          setLocale(localeFromLanguage(language));
        }
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  return locale;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function fmtWatts(value: number | null): string {
  return value === null ? "-" : `${value.toFixed(1)} W`;
}

function mappedText(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return map[value] ?? value;
}

function modeKey(mode: string | null | undefined): string {
  if (mode === "automatic" || mode === "observe" || mode === "off" || mode === "default") {
    return mode;
  }
  return "unknown";
}

function modeLabel(t: Copy, mode: string | null | undefined): string {
  return t.modes[modeKey(mode)] ?? t.modes.unknown;
}

function modeDescription(t: Copy, mode: string | null | undefined): string {
  return t.modeDescriptions[modeKey(mode)] ?? t.modeDescriptions.unknown;
}

const PluginTitle: FC = () => {
  const t = COPY[useLocale()];
  return <div className={staticClasses.Title}>{t.pluginName}</div>;
};

const GamePowerPanel: FC = () => {
  const t = COPY[useLocale()];
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [sample, setSample] = useState<SamplePayload | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const statusResult = await getStatus();
      setStatus(statusResult.service);
      setSample(await sampleOnce());
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const applyMode = async (mode: Mode) => {
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
    } catch (error) {
      setError(errorText(error));
      setNotice(null);
    } finally {
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
    } catch (error) {
      setError(errorText(error));
      setNotice(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const sampleTitle = sample?.appid ? `${t.game}: ${sample.appid}` : t.noSample;

  return (
    <>
      <PanelSection title={t.panelTitle}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {status
                ? `${t.currentMode}: ${modeLabel(t, status.mode)}`
                : busy
                  ? t.loading
                  : t.unavailable}
            </div>
            {status ? (
              <>
                <div>
                  {t.serviceState}: {status.active_state}/{status.sub_state}
                </div>
                <div style={detailStyle}>{mappedText(t.policyLabels, status.policy_label)}</div>
                <div style={detailStyle}>{modeDescription(t, status.mode)}</div>
              </>
            ) : null}
            {notice ? <div style={detailStyle}>{notice}</div> : null}
            {error ? (
              <div role="alert" style={detailStyle}>
                {t.errorPrefix}: {error}
              </div>
            ) : null}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyMode("automatic")}>
            {busy ? t.applying : t.modes.automatic}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyMode("observe")}>
            {busy ? t.applying : t.modes.observe}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyMode("off")}>
            {busy ? t.applying : t.modes.off}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t.sample}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {sampleTitle}
            </div>
            {sample ? (
              <>
                <div>
                  {t.action}: {mappedText(t.actions, sample.action)}
                </div>
                <div>
                  {t.package}: {fmtWatts(sample.package_w)}
                </div>
                <div>
                  {t.core}: {fmtWatts(sample.core_w)}
                </div>
                <div>
                  {t.graphics}: {fmtWatts(sample.uncore_w)}
                </div>
                {sample.reason ? (
                  <div style={detailStyle}>{mappedText(t.reasons, sample.reason)}</div>
                ) : null}
              </>
            ) : (
              <div style={detailStyle}>{busy ? t.loading : t.noSample}</div>
            )}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={load}>
            {busy ? t.applying : t.refresh}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={restore}>
            {busy ? t.applying : t.restore}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};

export default definePlugin(() => ({
  name: "Game Power",
  titleView: <PluginTitle />,
  content: <GamePowerPanel />,
  icon: <FaGamepad />,
}));
