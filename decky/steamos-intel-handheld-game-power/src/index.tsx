import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { FC, useEffect, useState } from "react";
import { FaGamepad } from "react-icons/fa";

type TargetState = {
  status: string;
  source: string;
  confidence: string;
  fps: number | null;
  target_frame_ms: number | null;
  raw: string | null;
};

type FrameSourceState = {
  status: string;
  source: string;
  confidence: string;
  avg_fps: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  sample_count: number | null;
  window_s: number | null;
};

type LearningState = {
  status: string;
  session_samples: number | null;
  positive_samples?: number | null;
  required_samples: number | null;
  required_sessions?: number | null;
  reusable_next_launch: boolean;
  skip_reason: string | null;
  hint_key?: string | null;
};

type EvidenceReadiness = {
  status: string;
  target_ready: boolean;
  frame_ready: boolean;
  learning_ready: boolean;
  claim_ready: boolean;
  control_ready: boolean;
  write_policy: string;
  reasons: string[];
};

type RuntimeSnapshot = {
  schema_version: string;
  timestamp_monotonic_s: number | null;
  source: string;
  mode: string | null;
  control_active: boolean;
  sample_source: string;
  appid: string | null;
  last_action: string | null;
  last_reason: string | null;
  classification_primary: string | null;
  classification_confidence: string | null;
  fps_target: TargetState;
  frame_source: FrameSourceState;
  package_w: number | null;
  core_w: number | null;
  uncore_w: number | null;
  pl1_w: number | null;
  render_busy: number | null;
  learning: LearningState;
  evidence_readiness: EvidenceReadiness;
  stale: boolean;
  error: string | null;
};

type ServiceStatus = {
  active_state: string;
  sub_state: string;
  mode: string;
  override_active: boolean;
  policy_label: string;
};

type FpsTargetOverride = {
  status: string;
  fps: number | null;
  source: string | null;
  supported_min: number;
  supported_max: number;
  supported_step: number;
};

type ControlStatus = {
  mode: string;
  effective_mode: string | null;
  override_active: boolean;
  policy_label: string;
  source: string | null;
  supported_modes: string[];
  fps_target_override: FpsTargetOverride;
};

type StatusPayload = {
  service: ServiceStatus;
  runtime: RuntimeSnapshot;
  control: ControlStatus;
};

type SamplePayload = {
  appid: string | null;
  sample_source: string;
  action: string | null;
  reason: string | null;
  package_w: number | null;
  core_w: number | null;
  uncore_w: number | null;
  pl1_w: number | null;
  render_busy: number | null;
  fps_target: TargetState;
  frame_source: FrameSourceState;
};

type Mode = "automatic" | "observe" | "off";

const getStatus = callable<[], StatusPayload>("get_status");
const sampleOnce = callable<[], SamplePayload>("sample_once");
const setMode = callable<[mode: Mode], { mode: Mode; policy_label: string }>("set_mode");
const setFpsTarget = callable<[fps: number | null], ControlStatus>("set_fps_target");
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
  telemetry: string;
  control: string;
  fpsTarget: string;
  targetAuto: string;
  targetManual: string;
  targetApply: string;
  learning: string;
  learningBeforeReuse: string;
  learningReady: string;
  learningNeedsTarget: string;
  learningStopped: string;
  manualProbe: string;
  probeNotice: string;
  action: string;
  game: string;
  package: string;
  core: string;
  graphics: string;
  noSample: string;
  refresh: string;
  readProbe: string;
  restore: string;
  applying: string;
  restored: string;
  evidenceLabel: string;
  evidenceStates: Record<string, string>;
  modes: Record<string, string>;
  modeDescriptions: Record<string, string>;
  telemetryLabels: Record<string, string>;
  targetStates: Record<string, string>;
  frameStates: Record<string, string>;
  actions: Record<string, string>;
  reasons: Record<string, string>;
  classifications: Record<string, string>;
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

const sliderStyle = {
  width: "100%",
  marginTop: "8px",
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

function fmtWatts(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${value.toFixed(1)} W`;
}

function fmtPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
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

function isTargetAwareReady(readiness: EvidenceReadiness | null | undefined): boolean {
  return readiness?.status === "target-aware-live" && readiness?.claim_ready === true;
}

function evidenceText(t: Copy, readiness: EvidenceReadiness | null | undefined): string {
  if (!readiness) {
    return t.evidenceStates.unavailable;
  }
  if (!isTargetAwareReady(readiness) && readiness.status === "target-aware-live") {
    return t.evidenceStates.unavailable;
  }
  return t.evidenceStates[readiness.status] ?? t.evidenceStates.unavailable;
}

function modeLabel(
  t: Copy,
  mode: string | null | undefined,
  runtime: RuntimeSnapshot | null,
): string {
  if (mode === "automatic" && isTargetAwareReady(runtime?.evidence_readiness)) {
    return t.telemetryLabels.targetAware;
  }
  return t.modes[modeKey(mode)] ?? t.modes.unknown;
}

function modeDescription(t: Copy, mode: string | null | undefined): string {
  return t.modeDescriptions[modeKey(mode)] ?? t.modeDescriptions.unknown;
}

function targetText(t: Copy, target: TargetState | null | undefined): string {
  if (!target) {
    return t.targetStates.unknown;
  }
  const label = mappedText(t.targetStates, target.status);
  return target.fps ? `${label}: ${target.fps.toFixed(0)} FPS` : label;
}

function frameText(t: Copy, frame: FrameSourceState | null | undefined): string {
  if (!frame) {
    return t.frameStates.missing;
  }
  const label = mappedText(t.frameStates, frame.status);
  return frame.avg_fps ? `${label}: ${frame.avg_fps.toFixed(1)} FPS` : label;
}

function learningText(t: Copy, learning: LearningState | null | undefined): string {
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

function runtimeHeadline(
  t: Copy,
  mode: string | null | undefined,
  runtime: RuntimeSnapshot | null,
): string {
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

const PluginTitle: FC = () => {
  const t = COPY[useLocale()];
  return <div className={staticClasses.Title}>{t.pluginName}</div>;
};

const GamePowerPanel: FC = () => {
  const t = COPY[useLocale()];
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [control, setControl] = useState<ControlStatus | null>(null);
  const [runtime, setRuntime] = useState<RuntimeSnapshot | null>(null);
  const [sample, setSample] = useState<SamplePayload | null>(null);
  const [manualFps, setManualFps] = useState(40);
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
      setControl(statusResult.control);
      setRuntime(statusResult.runtime);
      if (statusResult.control.fps_target_override.fps) {
        setManualFps(statusResult.control.fps_target_override.fps);
      } else if (statusResult.runtime.fps_target.fps) {
        setManualFps(statusResult.runtime.fps_target.fps);
      }
    } catch (error) {
      setError(errorText(error));
    } finally {
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
      setControl(statusResult.control);
      setRuntime(statusResult.runtime);
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
      setControl(statusResult.control);
      setRuntime(statusResult.runtime);
      setNotice(t.restored);
    } catch (error) {
      setError(errorText(error));
      setNotice(null);
    } finally {
      setBusy(false);
    }
  };

  const applyFpsTarget = async (fps: number | null) => {
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

  const runtimeTitle = runtime?.appid ? `${t.game}: ${runtime.appid}` : t.noSample;
  const probeTitle = sample?.appid ? `${t.game}: ${sample.appid}` : t.noSample;
  const supportedMin = control?.fps_target_override.supported_min ?? 30;
  const supportedMax = control?.fps_target_override.supported_max ?? 120;
  const supportedStep = control?.fps_target_override.supported_step ?? 5;
  const targetMode =
    control?.fps_target_override.status === "manual"
      ? `${t.targetManual}: ${control.fps_target_override.fps} FPS`
      : t.targetAuto;

  return (
    <>
      <PanelSection title={t.panelTitle}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {status
                ? `${t.currentMode}: ${modeLabel(t, status.mode, runtime)}`
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
                <div style={detailStyle}>{runtimeHeadline(t, status.mode, runtime)}</div>
                <div style={detailStyle}>
                  {t.evidenceLabel}: {evidenceText(t, runtime?.evidence_readiness)}
                </div>
                <div style={detailStyle}>{targetText(t, runtime?.fps_target)}</div>
                <div style={detailStyle}>{frameText(t, runtime?.frame_source)}</div>
                <div style={detailStyle}>
                  {t.learning}: {learningText(t, runtime?.learning)}
                </div>
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
      </PanelSection>

      <PanelSection title={t.control}>
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
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {t.fpsTarget}: {manualFps} FPS
            </div>
            <div style={detailStyle}>{targetMode}</div>
            <div style={detailStyle}>
              {supportedMin}-{supportedMax} FPS / {supportedStep}
            </div>
            <input
              aria-label={t.targetManual}
              type="range"
              min={30}
              max={120}
              step={5}
              value={manualFps}
              style={sliderStyle}
              onChange={(event) => setManualFps(Number(event.currentTarget.value))}
            />
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyFpsTarget(manualFps)}>
            {busy ? t.applying : t.targetApply}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyFpsTarget(null)}>
            {busy ? t.applying : t.targetAuto}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t.telemetry}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {runtimeTitle}
            </div>
            {runtime ? (
              <>
                <div>
                  {t.action}: {mappedText(t.actions, runtime.last_action)}
                </div>
                <div>
                  {t.package}: {fmtWatts(runtime.package_w)}
                </div>
                <div>
                  {t.core}: {fmtWatts(runtime.core_w)}
                </div>
                <div>
                  {t.graphics}: {fmtWatts(runtime.uncore_w)}
                </div>
                <div style={detailStyle}>
                  {mappedText(t.classifications, runtime.classification_primary)}
                </div>
                <div style={detailStyle}>{mappedText(t.reasons, runtime.last_reason)}</div>
                <div style={detailStyle}>Render: {fmtPercent(runtime.render_busy)}</div>
                <div style={detailStyle}>
                  {t.learning}: {learningText(t, runtime.learning)}
                </div>
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

      <PanelSection title={t.manualProbe}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div className={staticClasses.Title} style={titleStyle}>
              {probeTitle}
            </div>
            <div style={detailStyle}>{t.probeNotice}</div>
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
                <div style={detailStyle}>{targetText(t, sample.fps_target)}</div>
                <div style={detailStyle}>{frameText(t, sample.frame_source)}</div>
                {sample.reason ? (
                  <div style={detailStyle}>{mappedText(t.reasons, sample.reason)}</div>
                ) : null}
              </>
            ) : null}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={readProbe}>
            {busy ? t.applying : t.readProbe}
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
