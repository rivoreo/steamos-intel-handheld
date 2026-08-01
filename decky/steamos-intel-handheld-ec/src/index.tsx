import {
  ButtonItem,
  DropdownItem,
  staticClasses,
  PanelSection,
  PanelSectionRow,
  ToggleField,
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

type ApplyStatus = {
  current: ChargeStatus;
  target: ChargeStatus;
  applied: ChargeStatus;
  wrote: boolean;
  safety: string;
};

const getStatus = callable<[], ChargeStatus>("get_status");
const applyPreset = callable<[limit: number], ApplyStatus>("apply_limit");

// 100% is "no limit" from the user's point of view; calling it 100% invites the
// question of what the other numbers do to the battery.
const PRESETS = [60, 80, 100];

type LocaleKey = "en" | "zhHant";

type Copy = {
  pluginName: string;
  loading: string;
  unavailable: string;
  errorPrefix: string;
  // headline
  limitLabel: string;
  headline: (stop: number) => string;
  headlineUnlimited: string;
  restartNote: (stop: number, restart: number) => string;
  why: string;
  // control
  presetLabel: (limit: number) => string;
  presetUnlimited: string;
  applying: string;
  readOnlyWarning: string;
  // technical
  detailsToggle: string;
  detailsRegister: string;
  detailsValue: string;
  detailsWrites: string;
  detailsWritable: string;
  detailsReadOnly: string;
  refresh: string;
};

const COPY: Record<LocaleKey, Copy> = {
  en: {
    pluginName: "Charge Limit",
    loading: "Reading the charge limit...",
    unavailable: "Charge limit is unavailable",
    errorPrefix: "Error",
    limitLabel: "Charge limit",
    headline: (stop) => `Charging stops at ${stop}%`,
    headlineUnlimited: "Charging to full",
    restartNote: (stop, restart) =>
      `Charges back up once it falls below ${restart}%, so it settles between ${restart}% and ${stop}%.`,
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
    restartNote: (stop, restart) =>
      `掉到 ${restart}% 以下才會再充，所以電量會在 ${restart}% 到 ${stop}% 之間。`,
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
} as const;

const headlineStyle = {
  fontSize: "17px",
  fontWeight: 600,
  lineHeight: 1.25,
  whiteSpace: "normal",
  overflowWrap: "break-word",
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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDetails, setShowDetails] = useState(false);

  const loadStatus = async () => {
    setError(null);
    try {
      setStatus(await getStatus());
    } catch (err) {
      setStatus(null);
      setError(errorText(err));
    }
  };

  const applyLimit = async (limit: number) => {
    if (busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await applyPreset(limit);
      setStatus(result.applied);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
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
      : t.headline(stop!);

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div style={headlineStyle}>{headline}</div>
            {status && !unlimited && restart !== null ? (
              <div style={detailStyle}>{t.restartNote(stop!, restart)}</div>
            ) : null}
            {status && !status.writes_enabled ? (
              <div style={detailStyle}>{t.readOnlyWarning}</div>
            ) : null}
            {error ? (
              <div role="alert" style={detailStyle}>
                {t.errorPrefix}: {error}
              </div>
            ) : null}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection>
        <PanelSectionRow>
          <DropdownItem
            label={t.limitLabel}
            description={t.why}
            rgOptions={PRESETS.map((limit) => ({
              data: String(limit),
              label: limit >= 100 ? t.presetUnlimited : t.presetLabel(limit),
            }))}
            selectedOption={stop === null ? "" : String(stop)}
            disabled={busy || !status}
            onChange={(option) => applyLimit(Number(option.data))}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label={t.detailsToggle}
            checked={showDetails}
            onChange={setShowDetails}
          />
        </PanelSectionRow>
        {showDetails && status ? (
          <>
            <PanelSectionRow>
              <div style={blockStyle}>
                <div style={detailStyle}>
                  {t.detailsRegister}: {status.address_hex}
                </div>
                <div style={detailStyle}>
                  {t.detailsValue}: {status.raw_hex}
                </div>
                <div style={detailStyle}>
                  {t.detailsWrites}:{" "}
                  {status.writes_enabled ? t.detailsWritable : t.detailsReadOnly}
                </div>
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={loadStatus}>
                {busy ? t.applying : t.refresh}
              </ButtonItem>
            </PanelSectionRow>
          </>
        ) : null}
      </PanelSection>
    </>
  );
};

export default definePlugin(() => ({
  name: "Charge Limit",
  titleView: <PluginTitle />,
  content: <EcChargePanel />,
  icon: <FaBatteryHalf />,
  onDismount() {},
}));
