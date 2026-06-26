import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { FC, useEffect, useState } from "react";
import { FaBatteryHalf } from "react-icons/fa";

type ChargeStatus = {
  raw_hex: string;
  address_hex: string;
  start_threshold: number;
  end_threshold: number;
  restart_explanation: string;
  writes_enabled: boolean;
};

type PreviewStatus = {
  current: ChargeStatus;
  target: ChargeStatus;
  would_write: boolean;
  safety: string;
};

type ApplyStatus = {
  current: ChargeStatus;
  target: ChargeStatus;
  applied: ChargeStatus;
  wrote: boolean;
  safety: string;
};

const getStatus = callable<[], ChargeStatus>("get_status");
const previewPreset = callable<[limit: number], PreviewStatus>("preview_limit");
const applyPreset = callable<[limit: number], ApplyStatus>("apply_limit");

const PRESETS = [
  { value: 60, label: "60%" },
  { value: 80, label: "80%" },
  { value: 100, label: "100%" },
];

type LocaleKey = "en" | "zhHant";

type Copy = {
  pluginName: string;
  panelTitle: string;
  loadingTitle: string;
  loadingMessage: string;
  unavailableTitle: string;
  unavailableBody: string;
  errorPrefix: string;
  refresh: string;
  rawValue: string;
  rule: string;
  writeMode: string;
  readOnly: string;
  writeEnabled: string;
  writeNotice: string;
  setPreset: (limit: number) => string;
  previewing: (limit: number) => string;
  previewReady: (limit: number, rawHex: string) => string;
  applying: (limit: number) => string;
  appliedReady: (limit: number, rawHex: string) => string;
  stopRestart: (stop: number, restart: number) => string;
  restartRule: (stop: number, restart: number) => string;
};

const COPY: Record<LocaleKey, Copy> = {
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
} as const;

const titleStyle = {
  marginBottom: "8px",
} as const;

const detailStyle = {
  opacity: 0.72,
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

const PluginTitle: FC = () => {
  const t = COPY[useLocale()];
  return <div className={staticClasses.Title}>{t.pluginName}</div>;
};

const EcChargePanel: FC = () => {
  const t = COPY[useLocale()];
  const [status, setStatus] = useState<ChargeStatus | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = async () => {
    setNotice(null);
    setError(null);
    try {
      setStatus(await getStatus());
      setNotice(t.writeNotice);
    } catch (error) {
      setStatus(null);
      setError(errorText(error));
    }
  };

  const previewLimit = async (limit: number) => {
    setNotice(t.previewing(limit));
    setError(null);
    try {
      const result = await previewPreset(limit);
      setStatus(result.current);
      setNotice(t.previewReady(limit, result.target.raw_hex));
    } catch (error) {
      setError(errorText(error));
      setNotice(null);
    }
  };

  const applyLimit = async (limit: number) => {
    setNotice(t.applying(limit));
    setError(null);
    try {
      const result = await applyPreset(limit);
      setStatus(result.applied);
      setNotice(t.appliedReady(limit, result.applied.raw_hex));
    } catch (error) {
      setError(errorText(error));
      setNotice(null);
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  return (
    <PanelSection title={t.panelTitle}>
      <PanelSectionRow>
        <div style={blockStyle}>
          <div className={staticClasses.Title} style={titleStyle}>
            {status
              ? t.stopRestart(status.end_threshold, status.start_threshold)
              : error
                ? t.unavailableTitle
                : t.loadingTitle}
          </div>
          {status ? (
            <>
              <div>
                {t.rawValue}: {status.address_hex} = {status.raw_hex}
              </div>
              <div>
                {t.rule}: {t.restartRule(status.end_threshold, status.start_threshold)}
              </div>
              <div>
                {t.writeMode}: {status.writes_enabled ? t.writeEnabled : t.readOnly}
              </div>
            </>
          ) : (
            <>
              <div>{error ? t.unavailableBody : t.loadingMessage}</div>
              {error ? (
                <div style={detailStyle}>
                  {t.errorPrefix}: {error}
                </div>
              ) : null}
            </>
          )}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={loadStatus}>
          {t.refresh}
        </ButtonItem>
      </PanelSectionRow>
      {PRESETS.map((preset) => (
        <PanelSectionRow key={preset.value}>
          <ButtonItem layout="below" onClick={() => applyLimit(preset.value)}>
            {t.setPreset(preset.value)}
          </ButtonItem>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <div style={blockStyle}>{notice ?? (status ? t.writeNotice : t.loadingMessage)}</div>
      </PanelSectionRow>
    </PanelSection>
  );
};

export default definePlugin(() => ({
  name: "Charge Limit",
  titleView: <PluginTitle />,
  content: <EcChargePanel />,
  icon: <FaBatteryHalf />,
  onDismount() {},
}));
