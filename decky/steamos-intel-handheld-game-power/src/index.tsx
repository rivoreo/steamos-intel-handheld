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
  mode: string;
  service: string;
  sample: string;
  action: string;
  game: string;
  watts: string;
  package: string;
  core: string;
  graphics: string;
  noSample: string;
  refresh: string;
  restore: string;
  measured: string;
  applying: string;
  restored: string;
  automatic: string;
  observe: string;
  off: string;
  errorPrefix: string;
};

const COPY: Record<LocaleKey, Copy> = {
  en: {
    pluginName: "Game Power",
    panelTitle: "Game Power",
    loading: "Reading game-power status...",
    unavailable: "Game-power status is unavailable.",
    mode: "Mode",
    service: "Service",
    sample: "Latest sample",
    action: "Action",
    game: "Game",
    watts: "Watts",
    package: "Package",
    core: "CPU",
    graphics: "GPU side",
    noSample: "No foreground game sample",
    refresh: "Refresh",
    restore: "Restore defaults",
    measured: "Automatic mode uses measured balanced settings.",
    applying: "Applying...",
    restored: "Defaults restored.",
    automatic: "Automatic",
    observe: "Observe",
    off: "Off",
    errorPrefix: "Error",
  },
  zhHant: {
    pluginName: "遊戲電力",
    panelTitle: "遊戲電力",
    loading: "正在讀取遊戲電力狀態...",
    unavailable: "無法讀取遊戲電力狀態。",
    mode: "模式",
    service: "服務",
    sample: "最新樣本",
    action: "動作",
    game: "遊戲",
    watts: "瓦數",
    package: "封包",
    core: "CPU",
    graphics: "GPU 側",
    noSample: "目前沒有前景遊戲樣本",
    refresh: "重新讀取",
    restore: "還原預設",
    measured: "自動模式使用已測算的平衡設定。",
    applying: "正在套用...",
    restored: "已還原預設。",
    automatic: "自動",
    observe: "觀察",
    off: "關閉",
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
              {status ? `${t.mode}: ${status.mode}` : busy ? t.loading : t.unavailable}
            </div>
            {status ? (
              <>
                <div>
                  {t.service}: {status.active_state}/{status.sub_state}
                </div>
                <div style={detailStyle}>{status.policy_label}</div>
                <div style={detailStyle}>{t.measured}</div>
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
            {busy ? t.applying : t.automatic}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyMode("observe")}>
            {busy ? t.applying : t.observe}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyMode("off")}>
            {busy ? t.applying : t.off}
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
                  {t.action}: {sample.action ?? "-"}
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
                {sample.reason ? <div style={detailStyle}>{sample.reason}</div> : null}
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
