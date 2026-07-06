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

type ColorLedgerSummary = {
  color: string;
  entry_count: number;
  tid_count: number;
  actuator_states: Record<string, number>;
};

type ColorLedger = {
  truncated: boolean;
  colors: ColorLedgerSummary[];
};

type VerdictLedgerHealth = {
  status: string;
  reason: string | null;
  entry_count: number | null;
  path: string | null;
};

type GatedLane = {
  state: string;
  reason_codes: string[];
  variants?: string[];
  step?: number;
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
  phase?: string | null;
  phase_reason_codes?: string[];
  ladder_step?: number | null;
  color_ledger?: ColorLedger | null;
  verdict_ledger_health?: VerdictLedgerHealth | null;
  gated_lanes?: Record<string, GatedLane> | null;
  persona?: string | null;
  soft_pl1_w?: number | null;
  boost_active?: boolean | null;
  boost_reason?: string | null;
  trim_rungs_active?: string[] | null;
  frame_feed_status?: string | null;
  limiter_state?: string | null;
  stale: boolean;
  error: string | null;
};

type PersonaOverride = {
  status: string;
  persona: string | null;
  source: string | null;
  supported: string[];
};

type LimiterStatus = {
  status: string;
  fps: number | null;
  supported: boolean;
  source: string | null;
  raw?: string | null;
  supported_min?: number;
  supported_max?: number;
  supported_step?: number;
  clear_fps?: number;
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
  persona_override?: PersonaOverride;
};

type Persona = "battery" | "ac-quiet" | "ac-performance";

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
const setPersona = callable<[persona: Persona], ControlStatus>("set_persona");
const clearPersona = callable<[], ControlStatus>("clear_persona");
const getLimiter = callable<[], LimiterStatus>("limiter_status");
const applyLimiterFps = callable<[fps: number], LimiterStatus>("set_limiter");
const clearLimiterFps = callable<[], LimiterStatus>("clear_limiter");

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
  balanceLabel: string;
  phaseLabel: string;
  ladderLabel: string;
  colorsLabel: string;
  lanesLabel: string;
  verdictLabel: string;
  truncatedNote: string;
  none: string;
  personaTitle: string;
  personaCurrent: string;
  personaAuto: string;
  personaProvisional: string;
  personaLabels: Record<string, string>;
  limiterTitle: string;
  limiterConsentNote: string;
  limiterApply: string;
  limiterClear: string;
  limiterRead: string;
  limiterStates: Record<string, string>;
  budgetLabel: string;
  boostLabel: string;
  boostStates: Record<string, string>;
  frameFeedLabel: string;
  frameFeedStates: Record<string, string>;
  trimLabel: string;
  evidenceStates: Record<string, string>;
  phases: Record<string, string>;
  actuatorStates: Record<string, string>;
  laneNames: Record<string, string>;
  laneStates: Record<string, string>;
  verdictStates: Record<string, string>;
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
    balanceLabel: "Target balance",
    phaseLabel: "Phase",
    ladderLabel: "Trim ladder step",
    colorsLabel: "Thread colors",
    lanesLabel: "Gated write lanes",
    verdictLabel: "Verdict ledger",
    truncatedNote: "color sampling truncated",
    none: "none",
    personaTitle: "Power intent",
    personaCurrent: "Current intent",
    personaAuto: "Auto (match power source)",
    personaProvisional: "Framework shipped; tuning constants are provisional.",
    personaLabels: {
      battery: "Battery saver",
      "ac-quiet": "Quiet (plugged in)",
      "ac-performance": "Performance (plugged in)",
    },
    limiterTitle: "Frame limit helper",
    limiterConsentNote: "Opt-in: caps in-game frames through gamescope. Device-unverified.",
    limiterApply: "Apply frame limit",
    limiterClear: "Clear frame limit",
    limiterRead: "Check frame limit",
    limiterStates: {
      limited: "Frame limit active",
      unlimited: "No frame limit",
      unknown: "Frame limit helper available",
      unsupported: "Frame limit helper unavailable",
    },
    budgetLabel: "Soft power budget",
    boostLabel: "Boost",
    boostStates: {
      active: "active",
      idle: "idle",
    },
    frameFeedLabel: "Frame feed",
    frameFeedStates: {
      live: "live",
      stale: "stale",
      absent: "absent",
    },
    trimLabel: "Active trims",
    evidenceStates: {
      "target-aware-live": "Local target/frame evidence ready",
      "power-signals-only": "Local evidence: power signals only",
      "view-data-only": "View data only",
      stopped: "Game Power stopped",
      "control-invalid": "Local evidence unavailable",
      unavailable: "Local evidence unavailable",
    },
    phases: {
      "no-game": "No foreground game",
      loading: "Loading (constraints released)",
      "below-target-cpu-bound": "Below target, CPU-bound",
      "below-target-gpu-bound": "Below target, GPU-bound",
      "at-target": "At target",
      "above-target": "Above target (trimming)",
      "no-target": "No FPS target",
      unknown: "Unknown",
    },
    actuatorStates: {
      active: "active",
      advisory: "advisory",
      blocked: "blocked",
    },
    laneNames: {
      foreground: "Foreground boost",
      background: "Background easing",
      ladder: "Deep trim",
      other: "Write lane",
    },
    laneStates: {
      active: "active",
      blocked: "blocked (no verdict)",
      released: "released (loading)",
    },
    verdictStates: {
      ready: "verdicts loaded",
      unavailable: "no verdict ledger",
      missing: "verdict file missing",
      corrupt: "verdict file corrupt",
      invalid: "verdict file invalid",
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
    balanceLabel: "目標平衡",
    phaseLabel: "階段",
    ladderLabel: "降頻階梯步數",
    colorsLabel: "執行緒著色",
    lanesLabel: "受管制的寫入通道",
    verdictLabel: "裁決紀錄",
    truncatedNote: "著色取樣已截斷",
    none: "無",
    personaTitle: "電力取向",
    personaCurrent: "目前取向",
    personaAuto: "自動（依電源）",
    personaProvisional: "框架已上線；調校常數仍為暫定值。",
    personaLabels: {
      battery: "電池省電",
      "ac-quiet": "安靜（外接電源）",
      "ac-performance": "效能（外接電源）",
    },
    limiterTitle: "影格上限輔助",
    limiterConsentNote: "選用：透過 gamescope 設定遊戲影格上限。尚未在裝置驗證。",
    limiterApply: "套用影格上限",
    limiterClear: "取消影格上限",
    limiterRead: "檢查影格上限",
    limiterStates: {
      limited: "影格上限啟用中",
      unlimited: "沒有影格上限",
      unknown: "影格上限輔助可用",
      unsupported: "影格上限輔助不可用",
    },
    budgetLabel: "動態功耗預算",
    boostLabel: "衝刺",
    boostStates: {
      active: "啟用中",
      idle: "待命",
    },
    frameFeedLabel: "影格資料流",
    frameFeedStates: {
      live: "即時",
      stale: "過期",
      absent: "無",
    },
    trimLabel: "生效的節流",
    evidenceStates: {
      "target-aware-live": "本機 FPS 目標與影格資料可用",
      "power-signals-only": "本機證據：僅有功耗訊號",
      "view-data-only": "只看數據",
      stopped: "遊戲電力已停止",
      "control-invalid": "本機證據不可用",
      unavailable: "本機證據不可用",
    },
    phases: {
      "no-game": "沒有前景遊戲",
      loading: "載入中（已釋放限制）",
      "below-target-cpu-bound": "低於目標，CPU 受限",
      "below-target-gpu-bound": "低於目標，GPU 受限",
      "at-target": "已達目標",
      "above-target": "高於目標（正在降頻）",
      "no-target": "沒有 FPS 目標",
      unknown: "未知",
    },
    actuatorStates: {
      active: "啟用",
      advisory: "僅建議",
      blocked: "封鎖",
    },
    laneNames: {
      foreground: "前景提升",
      background: "背景讓路",
      ladder: "深層降頻",
      other: "寫入通道",
    },
    laneStates: {
      active: "啟用",
      blocked: "封鎖（無裁決）",
      released: "已釋放（載入中）",
    },
    verdictStates: {
      ready: "已載入裁決",
      unavailable: "沒有裁決紀錄",
      missing: "缺少裁決檔案",
      corrupt: "裁決檔案損毀",
      invalid: "裁決檔案無效",
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

function phaseText(t: Copy, phase: string | null | undefined): string {
  if (!phase) {
    return t.none;
  }
  return t.phases[phase] ?? phase;
}

function colorLedgerText(t: Copy, color: ColorLedgerSummary): string {
  const states = Object.entries(color.actuator_states)
    .map(([state, count]) => `${t.actuatorStates[state] ?? state}${count > 1 ? ` x${count}` : ""}`)
    .join(", ");
  const tail = states ? ` (${states})` : "";
  return `${color.color}: ${color.tid_count} TID / ${color.entry_count} role${tail}`;
}

function laneLabelKey(name: string): string {
  if (name.includes("foreground")) {
    return "foreground";
  }
  if (name.includes("background")) {
    return "background";
  }
  if (name.includes("ladder")) {
    return "ladder";
  }
  return "other";
}

function laneText(t: Copy, name: string, lane: GatedLane): string {
  const label = t.laneNames[laneLabelKey(name)] ?? t.laneNames.other;
  const state = t.laneStates[lane.state] ?? lane.state;
  const why = lane.reason_codes.length ? ` - ${lane.reason_codes.join(", ")}` : "";
  return `${label}: ${state}${why}`;
}

function verdictText(t: Copy, health: VerdictLedgerHealth): string {
  const state = t.verdictStates[health.status] ?? health.status;
  const count = health.entry_count === null ? "" : ` (${health.entry_count})`;
  return `${state}${count}`;
}

function personaLabel(t: Copy, persona: string | null | undefined): string {
  if (!persona) {
    return t.personaAuto;
  }
  return t.personaLabels[persona] ?? persona;
}

function activePersona(control: ControlStatus | null): string | null {
  const override = control?.persona_override;
  if (override?.status === "manual" && override.persona) {
    return override.persona;
  }
  return null;
}

function frameFeedText(t: Copy, status: string | null | undefined): string {
  if (!status) {
    return t.none;
  }
  return t.frameFeedStates[status] ?? status;
}

function limiterStateText(t: Copy, limiter: LimiterStatus | null): string {
  if (!limiter) {
    return "-";
  }
  const label = t.limiterStates[limiter.status] ?? limiter.status;
  return limiter.fps ? `${label}: ${limiter.fps} FPS` : label;
}

const TargetBalanceLiveRow: FC<{ t: Copy; runtime: RuntimeSnapshot | null }> = ({ t, runtime }) => {
  if (!runtime || runtime.stale || runtime.error || !runtime.persona) {
    return null;
  }
  const { soft_pl1_w, boost_active, frame_feed_status, trim_rungs_active } = runtime;
  const boostText = boost_active ? t.boostStates.active : t.boostStates.idle;
  const trims = trim_rungs_active && trim_rungs_active.length ? trim_rungs_active.join(", ") : t.none;
  return (
    <>
      <div style={detailStyle}>
        {t.personaCurrent}: {personaLabel(t, runtime.persona)}
      </div>
      <div style={detailStyle}>
        {t.package}: {fmtWatts(runtime.package_w)} / {t.budgetLabel}:{" "}
        {soft_pl1_w === null || soft_pl1_w === undefined ? t.none : fmtWatts(soft_pl1_w)}
      </div>
      <div style={detailStyle}>
        {t.boostLabel}: {boostText}
      </div>
      <div style={detailStyle}>
        {t.frameFeedLabel}: {frameFeedText(t, frame_feed_status)}
      </div>
      <div style={detailStyle}>
        {t.trimLabel}: {trims}
      </div>
    </>
  );
};

const TargetBalanceDetails: FC<{ t: Copy; runtime: RuntimeSnapshot | null }> = ({ t, runtime }) => {
  if (!runtime || runtime.stale || runtime.error) {
    return null;
  }
  const { phase, ladder_step, color_ledger, verdict_ledger_health, gated_lanes } = runtime;
  if (
    phase === undefined ||
    phase === null ||
    (ladder_step === undefined || ladder_step === null) &&
      !color_ledger &&
      !verdict_ledger_health &&
      !gated_lanes
  ) {
    return null;
  }
  const lanes = gated_lanes ? Object.entries(gated_lanes) : [];
  return (
    <>
      <div style={detailStyle}>
        {t.balanceLabel} - {t.phaseLabel}: {phaseText(t, phase)}
      </div>
      {ladder_step !== undefined && ladder_step !== null ? (
        <div style={detailStyle}>
          {t.ladderLabel}: S{ladder_step}
        </div>
      ) : null}
      {verdict_ledger_health ? (
        <div style={detailStyle}>
          {t.verdictLabel}: {verdictText(t, verdict_ledger_health)}
        </div>
      ) : null}
      {color_ledger && color_ledger.colors.length ? (
        <div style={detailStyle}>
          {t.colorsLabel}: {color_ledger.colors.map((color) => colorLedgerText(t, color)).join(" | ")}
          {color_ledger.truncated ? ` (${t.truncatedNote})` : ""}
        </div>
      ) : null}
      {lanes.length ? (
        <div style={detailStyle}>
          {t.lanesLabel}: {lanes.map(([name, lane]) => laneText(t, name, lane)).join(" | ")}
        </div>
      ) : null}
    </>
  );
};

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
  const [limiter, setLimiter] = useState<LimiterStatus | null>(null);
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

  const applyPersona = async (persona: Persona | null) => {
    if (busy) {
      return;
    }
    setBusy(true);
    setNotice(t.applying);
    setError(null);
    try {
      if (persona === null) {
        await clearPersona();
      } else {
        await setPersona(persona);
      }
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

  const runLimiter = async (action: "read" | "apply" | "clear") => {
    if (busy) {
      return;
    }
    setBusy(true);
    setNotice(action === "read" ? null : t.applying);
    setError(null);
    try {
      if (action === "apply") {
        setLimiter(await applyLimiterFps(manualFps));
      } else if (action === "clear") {
        setLimiter(await clearLimiterFps());
      } else {
        setLimiter(await getLimiter());
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

  const currentPersona = activePersona(control);
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

      <PanelSection title={t.personaTitle}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div style={detailStyle}>
              {t.personaCurrent}: {currentPersona ? personaLabel(t, currentPersona) : t.personaAuto}
            </div>
            <div style={detailStyle}>{t.personaProvisional}</div>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyPersona("battery")}>
            {busy ? t.applying : t.personaLabels.battery}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyPersona("ac-quiet")}>
            {busy ? t.applying : t.personaLabels["ac-quiet"]}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyPersona("ac-performance")}>
            {busy ? t.applying : t.personaLabels["ac-performance"]}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => applyPersona(null)}>
            {busy ? t.applying : t.personaAuto}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t.limiterTitle}>
        <PanelSectionRow>
          <div style={blockStyle}>
            <div style={detailStyle}>{t.limiterConsentNote}</div>
            <div style={detailStyle}>{limiterStateText(t, limiter)}</div>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => runLimiter("read")}>
            {busy ? t.applying : t.limiterRead}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => runLimiter("apply")}>
            {busy ? t.applying : `${t.limiterApply}: ${manualFps} FPS`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => runLimiter("clear")}>
            {busy ? t.applying : t.limiterClear}
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
                <TargetBalanceLiveRow t={t} runtime={runtime} />
                <TargetBalanceDetails t={t} runtime={runtime} />
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
