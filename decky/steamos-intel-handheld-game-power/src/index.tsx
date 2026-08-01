import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { FC, useEffect, useRef, useState } from "react";
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
  p95_baseline_ms?: number | null;
  p95_budget_ms?: number | null;
  auto_target?: AutoTargetState | null;
  stale: boolean;
  error: string | null;
};

type AutoTargetState = {
  status: string;
  refresh_hz?: number | null;
  candidates?: number[];
  drops_this_session?: number;
  input_idle_s?: number | null;
  cap_applied_fps?: number | null;
  cap_reason?: string | null;
  gpu?: {
    render_busy: number | null;
    c6_ms: number | null;
    actual_mhz: number | null;
    saturated: boolean | null;
  } | null;
  proposal?: {
    fps: number;
    reason: string;
    sustainable_fps: number;
    samples: number;
  } | null;
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

/**
 * One user-facing choice instead of two orthogonal ones.
 *
 * The daemon separates "mode" (automatic/observe/off) from "persona"
 * (battery/ac-quiet/ac-performance), and persona silently does nothing unless
 * mode is automatic. Nobody can be expected to hold that in their head mid-game,
 * so the panel presents a single list and maps it back to the pair.
 */
type Profile = "auto" | "battery" | "quiet" | "performance" | "observe" | "off";

const PROFILE_ORDER: Profile[] = [
  "auto",
  "battery",
  "quiet",
  "performance",
  "observe",
  "off",
];

const PROFILE_TO_PERSONA: Partial<Record<Profile, Persona>> = {
  battery: "battery",
  quiet: "ac-quiet",
  performance: "ac-performance",
};

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
  // --- headline states ---
  status: Record<string, string>;
  holdingWithTarget: (state: string, fps: string) => string;
  nowLine: (fps: string, watts: string) => string;
  // --- profile ---
  profileLabel: string;
  profileDescription: string;
  profiles: Record<Profile, string>;
  profileHints: Record<Profile, string>;
  // --- fps target ---
  targetLabel: string;
  targetAuto: string;
  targetAutoDescription: () => string;
  targetAutoDetected: (fps: string) => string;
  targetFixed: (fps: string) => string;
  targetManualDescription: (fps: string) => string;
  // --- unreachable-target advice ---
  unreachableTitle: string;
  unreachableBody: (target: string, actual: string) => string;
  unreachableAction: (fps: string) => string;
  // --- shared ---
  applying: string;
  restored: string;
  errorPrefix: string;
  // --- diagnostics (opt-in) ---
  diagnosticsToggle: string;
  diagnosticsDescription: string;
  diag: {
    doing: string;
    activityNothing: string;
    activityTrimming: string;
    activityFullPower: string;
    activityLoading: string;
    activityCapped: (fps: string) => string;
    activityNoGame: string;
    power: string;
    core: string;
    graphics: string;
    slowestFrames: string;
    slowestFramesValue: (ms: string, fps: string) => string;
    gpuLoad: string;
    service: string;
    serviceOk: string;
    serviceProblem: (raw: string) => string;
    learning: string;
    restore: string;
    refresh: string;
  };
  phases: Record<string, string>;
  actions: Record<string, string>;
  actuatorStates: Record<string, string>;
  laneNames: Record<string, string>;
  laneStates: Record<string, string>;
  verdictStates: Record<string, string>;
  evidenceStates: Record<string, string>;
  frameStates: Record<string, string>;
  targetStates: Record<string, string>;
  limiterStates: Record<string, string>;
  learningStates: Record<string, string>;
  none: string;
};

const COPY: Record<LocaleKey, Copy> = {
  en: {
    pluginName: "Game Power",
    panelTitle: "Game Power",
    loading: "Checking...",
    unavailable: "Game Power is unavailable",
    status: {
      off: "Turned off",
      observe: "Watching only",
      noGame: "Waiting for a game",
      noTarget: "No frame rate target set",
      stale: "Reconnecting...",
      starting: "Warming up...",
      loadingScene: "Loading - full power",
      boosting: "Full power",
      holding: "Holding steady",
      holdingSaving: "Holding steady, using less power",
    },
    holdingWithTarget: (state, fps) => `${state} at ${fps} FPS`,
    nowLine: (fps, watts) => `${fps} FPS now, ${watts}`,
    profileLabel: "Power profile",
    profileDescription: "How much power to spend holding your frame rate.",
    profiles: {
      auto: "Automatic",
      battery: "Save battery",
      quiet: "Quiet",
      performance: "Performance",
      observe: "Watch only",
      off: "Off",
    },
    profileHints: {
      auto: "Follows whether you are on battery or plugged in.",
      battery: "Lowest power that still holds your frame rate.",
      quiet: "Keeps power down so fans stay quiet.",
      performance: "Spends full power for the most headroom.",
      observe: "Collects data without changing anything.",
      off: "Game Power makes no changes at all.",
    },
    targetLabel: "Frame rate target",
    targetAuto: "Automatic",
    targetAutoDescription: () => "Follows the SteamOS limit and lowers it if a scene cannot hold it.",
    targetAutoDetected: (fps) => `Automatic (now ${fps} FPS)`,
    targetFixed: (fps) => `${fps} FPS`,
    targetManualDescription: (fps) => `Set by you: ${fps} FPS.`,
    unreachableTitle: "Target looks out of reach",
    unreachableBody: (target, actual) =>
      `This scene is running near ${actual} FPS at full power, below your ${target} FPS target.`,
    unreachableAction: (fps) => `Set the target to ${fps} FPS`,
    applying: "Applying...",
    restored: "Restored",
    errorPrefix: "Error",
    diagnosticsToggle: "Show technical details",
    diagnosticsDescription: "Live scheduler internals. Not needed for normal use.",
    diag: {
      doing: "Doing",
      activityNothing: "Nothing - the game is within its target",
      activityTrimming: "Lowering power while holding the target",
      activityFullPower: "Full power - the target is not being met",
      activityLoading: "Full power - the game is loading",
      activityCapped: (fps: string) => `Holding a ${fps} FPS cap`,
      activityNoGame: "Nothing - no game is running",
      power: "Power draw",
      core: "CPU",
      graphics: "graphics",
      slowestFrames: "Slowest frames",
      slowestFramesValue: (ms: string, fps: string) => `${ms} (about ${fps} FPS)`,
      gpuLoad: "Graphics load",
      service: "Background service",
      serviceOk: "Running normally",
      serviceProblem: (raw: string) => `Not running normally (${raw})`,
      learning: "Learning",
      restore: "Restore defaults",
      refresh: "Refresh now",
    },
    phases: {
      "no-game": "No game",
      "no-target": "No target",
      loading: "Loading",
      "at-target": "At target",
      "above-target": "Above target",
      "below-target-cpu-bound": "Below target (CPU)",
      "below-target-gpu-bound": "Below target (graphics)",
      unknown: "Unknown",
    },
    actions: {
      idle: "Idle",
      "observe-only": "Observing",
      restore: "Restored",
      "gpu-priority-epp": "Graphics priority",
      "target-balance-trim": "Trimming",
      "target-balance-release": "Released",
      "loading-boost": "Loading boost",
    },
    actuatorStates: { active: "active", blocked: "blocked", pending: "pending" },
    laneNames: {
      foreground: "Foreground",
      background: "Background",
      ladder: "Deep steps",
      other: "Other",
    },
    laneStates: { active: "active", blocked: "blocked", released: "released" },
    verdictStates: {
      ready: "ready",
      unavailable: "unavailable",
      invalid: "invalid",
      missing: "missing",
    },
    evidenceStates: {
      "target-aware-live": "Target and frame data ready",
      "power-signals-only": "Power signals only",
      unavailable: "Unavailable",
      stopped: "Stopped",
      "view-data-only": "View data only",
    },
    frameStates: {
      live: "Live",
      missing: "Missing",
      stale: "Stale",
      "profiler-only": "Profiler only",
    },
    targetStates: {
      known: "Known",
      unknown: "Unknown",
      unlimited: "Unlimited",
      "none-configured": "Not configured",
    },
    limiterStates: {
      unknown: "Unknown",
      unsupported: "Not supported",
      applied: "Applied",
      cleared: "Cleared",
      ready: "Ready",
    },
    learningStates: {
      ready: "Can reuse next launch",
      learning: "Learning before reuse",
      needsTarget: "Needs a steady target",
      stopped: "Stopped",
    },
    none: "None",
  },
  zhHant: {
    pluginName: "遊戲電力",
    panelTitle: "遊戲電力",
    loading: "檢查中...",
    unavailable: "遊戲電力無法使用",
    status: {
      off: "已關閉",
      observe: "只觀察",
      noGame: "等待遊戲中",
      noTarget: "尚未設定張數目標",
      stale: "重新連線中...",
      starting: "暖機中...",
      loadingScene: "載入中 - 全力輸出",
      boosting: "全力輸出",
      holding: "穩定維持中",
      holdingSaving: "穩定維持中，功耗已降低",
    },
    holdingWithTarget: (state, fps) => `${fps} FPS ${state}`,
    nowLine: (fps, watts) => `目前 ${fps} FPS，${watts}`,
    profileLabel: "電力模式",
    profileDescription: "決定要花多少功耗來維持你的張數。",
    profiles: {
      auto: "自動",
      battery: "省電",
      quiet: "安靜",
      performance: "效能",
      observe: "只觀察",
      off: "關閉",
    },
    profileHints: {
      auto: "依照使用電池或插電自動切換。",
      battery: "在維持張數的前提下用最低功耗。",
      quiet: "壓低功耗，讓風扇保持安靜。",
      performance: "全力輸出，保留最多餘裕。",
      observe: "只收集資料，不做任何調整。",
      off: "遊戲電力完全不介入。",
    },
    targetLabel: "張數目標",
    targetAuto: "自動",
    targetAutoDescription: () => "跟隨 SteamOS 的限制；場景撐不住時會自動調低。",
    targetAutoDetected: (fps) => `自動（目前 ${fps} FPS）`,
    targetFixed: (fps) => `${fps} FPS`,
    targetManualDescription: (fps) => `你設定的目標：${fps} FPS。`,
    unreachableTitle: "目標似乎達不到",
    unreachableBody: (target, actual) =>
      `這個場景在全力輸出下大約只有 ${actual} FPS，低於你設定的 ${target} FPS。`,
    unreachableAction: (fps) => `把目標改成 ${fps} FPS`,
    applying: "套用中...",
    restored: "已還原",
    errorPrefix: "錯誤",
    diagnosticsToggle: "顯示技術細節",
    diagnosticsDescription: "調度器的即時內部狀態，一般使用不需要看。",
    diag: {
      doing: "正在做什麼",
      activityNothing: "沒有介入 —— 遊戲在目標之內",
      activityTrimming: "維持目標的同時降低功耗",
      activityFullPower: "全力輸出 —— 目標未達成",
      activityLoading: "全力輸出 —— 遊戲載入中",
      activityCapped: (fps: string) => `限制在 ${fps} FPS`,
      activityNoGame: "沒有介入 —— 目前沒有遊戲在跑",
      power: "功耗",
      core: "處理器",
      graphics: "繪圖",
      slowestFrames: "最慢的那些影格",
      slowestFramesValue: (ms: string, fps: string) => `${ms}（約 ${fps} FPS）`,
      gpuLoad: "繪圖負載",
      service: "背景服務",
      serviceOk: "正常運作中",
      serviceProblem: (raw: string) => `沒有正常運作（${raw}）`,
      learning: "學習",
      restore: "還原預設",
      refresh: "立即重新整理",
    },
    phases: {
      "no-game": "沒有遊戲",
      "no-target": "沒有目標",
      loading: "載入中",
      "at-target": "已達目標",
      "above-target": "超過目標",
      "below-target-cpu-bound": "未達目標（處理器）",
      "below-target-gpu-bound": "未達目標（繪圖）",
      unknown: "未知",
    },
    actions: {
      idle: "閒置",
      "observe-only": "觀察中",
      restore: "已還原",
      "gpu-priority-epp": "繪圖優先",
      "target-balance-trim": "調整中",
      "target-balance-release": "已放開",
      "loading-boost": "載入增壓",
    },
    actuatorStates: { active: "作用中", blocked: "阻擋", pending: "等待中" },
    laneNames: {
      foreground: "前景",
      background: "背景",
      ladder: "深層階梯",
      other: "其他",
    },
    laneStates: { active: "作用中", blocked: "阻擋", released: "已釋放" },
    verdictStates: {
      ready: "可用",
      unavailable: "不可用",
      invalid: "無效",
      missing: "不存在",
    },
    evidenceStates: {
      "target-aware-live": "目標與影格資料可用",
      "power-signals-only": "僅有功耗訊號",
      unavailable: "不可用",
      stopped: "已停止",
      "view-data-only": "只看數據",
    },
    frameStates: {
      live: "即時",
      missing: "缺少",
      stale: "過期",
      "profiler-only": "僅分析工具",
    },
    targetStates: {
      known: "已知",
      unknown: "未知",
      unlimited: "無限制",
      "none-configured": "未設定",
    },
    limiterStates: {
      unknown: "未知",
      unsupported: "不支援",
      applied: "已套用",
      cleared: "已清除",
      ready: "就緒",
    },
    learningStates: {
      ready: "下次可直接套用",
      learning: "學習中，暫不復用",
      needsTarget: "需要穩定的目標",
      stopped: "已停止",
    },
    none: "無",
  },
};

const headlineStyle = {
  fontSize: "17px",
  fontWeight: 600,
  lineHeight: 1.25,
  whiteSpace: "normal",
  overflowWrap: "break-word",
} as const;

const nowStyle = {
  opacity: 0.75,
  fontSize: "13px",
  marginTop: "4px",
} as const;

const blockStyle = {
  width: "100%",
  minWidth: 0,
  whiteSpace: "normal",
  overflowWrap: "break-word",
  lineHeight: 1.28,
} as const;

const detailStyle = {
  opacity: 0.74,
  fontSize: "12px",
  marginTop: "3px",
  whiteSpace: "normal",
  overflowWrap: "anywhere",
} as const;

const noticeStyle = {
  ...blockStyle,
  fontSize: "13px",
  marginTop: "2px",
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

function fmtMs(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${value.toFixed(1)} ms`;
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

function isBelowTarget(phase: string | null | undefined): boolean {
  return phase === "below-target-cpu-bound" || phase === "below-target-gpu-bound";
}

function activeProfile(control: ControlStatus | null): Profile {
  if (!control) {
    return "auto";
  }
  if (control.mode === "off") {
    return "off";
  }
  if (control.mode === "observe") {
    return "observe";
  }
  const override = control.persona_override;
  if (override?.status === "manual" && override.persona) {
    if (override.persona === "battery") {
      return "battery";
    }
    if (override.persona === "ac-quiet") {
      return "quiet";
    }
    if (override.persona === "ac-performance") {
      return "performance";
    }
  }
  return "auto";
}

/** Plain-language answer to "what is it doing right now". */
function headlineText(
  t: Copy,
  control: ControlStatus | null,
  runtime: RuntimeSnapshot | null,
): string {
  if (!control) {
    return t.loading;
  }
  if (control.mode === "off") {
    return t.status.off;
  }
  if (control.mode === "observe") {
    return t.status.observe;
  }
  if (!runtime || runtime.error) {
    return t.unavailable;
  }
  if (runtime.stale) {
    return t.status.stale;
  }
  if (!runtime.appid) {
    return t.status.noGame;
  }
  const target = runtime.fps_target.fps;
  if (!target) {
    return t.status.noTarget;
  }
  const phase = runtime.phase;
  if (phase === "loading") {
    return t.status.loadingScene;
  }
  if (isBelowTarget(phase)) {
    return t.status.boosting;
  }
  if (phase === "at-target" || phase === "above-target") {
    const trimmed = (runtime.trim_rungs_active?.length ?? 0) > 0;
    return trimmed ? t.status.holdingSaving : t.status.holding;
  }
  return t.status.starting;
}

/**
 * Sustained "below target even with nothing trimmed" means the target itself is
 * the problem, not the scheduler. Suggest a reachable one rather than silently
 * burning full power forever.
 */
function unreachableSuggestion(avgFps: number, step: number): number {
  const floor = Math.floor(avgFps / step) * step;
  return Math.max(step, floor);
}

const StatusCard: FC<{
  t: Copy;
  control: ControlStatus | null;
  runtime: RuntimeSnapshot | null;
  notice: string | null;
  error: string | null;
}> = ({ t, control, runtime, notice, error }) => {
  const target = runtime?.fps_target.fps ?? null;
  const headline = headlineText(t, control, runtime);
  const showNow =
    control?.mode !== "off" && runtime && !runtime.error && !runtime.stale && runtime.appid;
  const avg = runtime?.frame_source.avg_fps ?? null;
  return (
    <PanelSectionRow>
      <div style={blockStyle}>
        <div style={headlineStyle}>
          {target && (headline === t.status.holding || headline === t.status.holdingSaving)
            ? t.holdingWithTarget(headline, target.toFixed(0))
            : headline}
        </div>
        {showNow ? (
          <div style={nowStyle}>
            {t.nowLine(avg === null ? "-" : avg.toFixed(0), fmtWatts(runtime?.package_w))}
          </div>
        ) : null}
        {notice ? <div style={noticeStyle}>{notice}</div> : null}
        {error ? (
          <div role="alert" style={noticeStyle}>
            {t.errorPrefix}: {error}
          </div>
        ) : null}
      </div>
    </PanelSectionRow>
  );
};

const Diagnostics: FC<{
  t: Copy;
  status: ServiceStatus | null;
  runtime: RuntimeSnapshot | null;
}> = ({ t, status, runtime }) => {
  if (!runtime) {
    return null;
  }
  const rows: [string, string][] = [];
  // Deliberately not shown: ladder step, rung ids, thread-group tallies, gated
  // lanes, verdict ledger. They are scheduler bookkeeping with no meaning to
  // anyone who is not us, and the runtime snapshot carries them for debugging.
  rows.push([t.diag.doing, describeActivity(t, runtime)]);
  rows.push([
    t.diag.power,
    `${fmtWatts(runtime.package_w)} (${t.diag.core} ${fmtWatts(runtime.core_w)} / ${t.diag.graphics} ${fmtWatts(runtime.uncore_w)})`,
  ]);
  // Frame timing only means anything while a game is drawing. With the machine
  // sitting at the library the compositor idles, which drags the pacing budget
  // up to values like 6463 ms - a real number that reads as a broken one.
  const p95 = runtime.appid ? runtime.frame_source.p95_ms : null;
  if (p95) {
    rows.push([
      t.diag.slowestFrames,
      t.diag.slowestFramesValue(fmtMs(p95), String(Math.round(1000 / p95))),
    ]);
  }
  const gpu = runtime.auto_target?.gpu;
  if (gpu && gpu.render_busy !== null && gpu.render_busy !== undefined) {
    rows.push([t.diag.gpuLoad, fmtPercent(gpu.render_busy)]);
  }
  if (status) {
    rows.push([t.diag.service, describeService(t, status)]);
  }

  return (
    <PanelSectionRow>
      <div style={blockStyle}>
        {rows.map(([label, value]) => (
          <div key={label} style={detailStyle}>
            {label}: {value}
          </div>
        ))}
      </div>
    </PanelSectionRow>
  );
};

/**
 * systemd's own vocabulary ("active/running") is not something a Steam Deck
 * user should have to learn. Say it plainly, and keep the raw pair only when
 * something is wrong, where it is the thing worth pasting into a bug report.
 */
function describeService(t: Copy, status: ServiceStatus): string {
  const raw = `${status.active_state}/${status.sub_state}`;
  if (status.active_state === "active" && status.sub_state === "running") {
    return t.diag.serviceOk;
  }
  return t.diag.serviceProblem(raw);
}

function describeActivity(t: Copy, runtime: RuntimeSnapshot): string {
  const trims = runtime.trim_rungs_active?.length ?? 0;
  const cap = runtime.auto_target?.cap_applied_fps ?? null;
  if (!runtime.appid) {
    return t.diag.activityNoGame;
  }
  if (cap) {
    return t.diag.activityCapped(String(cap));
  }
  if (runtime.phase === "loading") {
    return t.diag.activityLoading;
  }
  if (isBelowTarget(runtime.phase)) {
    return t.diag.activityFullPower;
  }
  return trims > 0 ? t.diag.activityTrimming : t.diag.activityNothing;
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
  // null == "follow whatever the daemon detected". Never seed a literal here:
  // a hardcoded default renders as a real FPS target the user never chose, and
  // one tap on Set/Apply would commit it.
  const [manualFps, setManualFps] = useState<number | null>(null);
  const [limiter, setLimiter] = useState<LimiterStatus | null>(null);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Consecutive polls spent below target with nothing of ours applied.
  const [starvedPolls, setStarvedPolls] = useState(0);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const absorb = (result: StatusPayload) => {
    setStatus(result.service);
    setControl(result.control);
    setRuntime(result.runtime);
    const detected = result.control.fps_target_override.fps ?? result.runtime.fps_target.fps;
    if (detected) {
      // Only seed the slider; never clobber a value the user is dragging.
      setManualFps((current) => current ?? detected);
    }
    // "Full power and still short" only counts when we are not the cause.
    const starved =
      isBelowTarget(result.runtime.phase) &&
      (result.runtime.trim_rungs_active?.length ?? 0) === 0 &&
      !!result.runtime.fps_target.fps;
    setStarvedPolls((current) => (starved ? current + 1 : 0));
  };

  const load = async () => {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      absorb(await getStatus());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  // The panel is the only place the live governor is observable, so keep it
  // ticking instead of showing whatever was true when it was opened.
  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(async () => {
      if (busyRef.current) {
        return;
      }
      try {
        const result = await getStatus();
        if (!cancelled) {
          absorb(result);
        }
      } catch {
        // Transient backend hiccup; the next tick retries.
      }
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const guard = async (work: () => Promise<void>) => {
    if (busy) {
      return;
    }
    setBusy(true);
    setNotice(t.applying);
    setError(null);
    try {
      await work();
      setNotice(null);
    } catch (err) {
      setError(errorText(err));
      setNotice(null);
    } finally {
      setBusy(false);
    }
  };

  const applyProfile = (profile: Profile) =>
    guard(async () => {
      if (profile === "off" || profile === "observe") {
        await setMode(profile === "off" ? "off" : "observe");
      } else {
        await setMode("automatic");
        const persona = PROFILE_TO_PERSONA[profile];
        if (persona) {
          await setPersona(persona);
        } else {
          await clearPersona();
        }
      }
      absorb(await getStatus());
    });

  const applyFpsTarget = (fps: number | null) =>
    guard(async () => {
      await setFpsTarget(fps);
      // Clearing the override hands the slider back to auto-detect, so drop the
      // local value instead of pinning the old one.
      if (fps === null) {
        setManualFps(null);
      }
      setStarvedPolls(0);
      absorb(await getStatus());
    });

  const runLimiter = (action: "read" | "apply" | "clear") =>
    guard(async () => {
      if (action === "read") {
        setLimiter(await getLimiter());
      } else if (action === "apply") {
        setLimiter(await applyLimiterFps(sliderFps));
      } else {
        setLimiter(await clearLimiterFps());
      }
    });

  const restore = () =>
    guard(async () => {
      await restoreDefaults();
      absorb(await getStatus());
      setNotice(t.restored);
    });

  const readProbe = () =>
    guard(async () => {
      setSample(await sampleOnce());
    });

  const supportedMin = control?.fps_target_override.supported_min ?? 30;
  const supportedMax = control?.fps_target_override.supported_max ?? 120;
  const supportedStep = control?.fps_target_override.supported_step ?? 5;
  const detectedFps = control?.fps_target_override.fps ?? runtime?.fps_target.fps ?? null;
  const manualTarget = control?.fps_target_override.status === "manual";
  // Offer only rates the panel can actually pace evenly: exact divisors of the
  // current refresh rate, as computed by the daemon. There is no working VRR
  // here, so an off-divisor target judders no matter how well we schedule it.
  const reported = (runtime?.auto_target?.candidates ?? []).filter(
    (fps) => fps >= supportedMin && fps <= supportedMax,
  );
  // Never let a missing field kill the control. If the daemon has not reported
  // its divisor list, fall back to the standard divisors of a 120 Hz panel plus
  // whatever target is currently detected, so the user can still choose one.
  // A greyed-out dropdown with no explanation is the worst possible outcome.
  const fallback = [120, 60, 40, 30].filter(
    (fps) => fps >= supportedMin && fps <= supportedMax,
  );
  const candidates = reported.length
    ? reported
    : Array.from(
        new Set([...(detectedFps ? [Math.round(detectedFps)] : []), ...fallback]),
      ).sort((a, b) => b - a);
  // "auto" is a real choice, not the absence of one.
  const targetOptions: { data: string; label: string }[] = [
    { data: "auto", label: manualTarget ? t.targetAuto : t.targetAutoDetected(
        detectedFps === null ? "-" : detectedFps.toFixed(0)) },
    ...candidates.map((fps) => ({ data: String(fps), label: t.targetFixed(String(fps)) })),
  ];
  const selectedTarget = manualTarget ? String(control?.fps_target_override.fps ?? "") : "auto";
  // The limiter helper still needs a concrete number to apply.
  const sliderFps = manualFps ?? detectedFps ?? candidates[0] ?? supportedMax;
  const profile = activeProfile(control);
  const targetFps = runtime?.fps_target.fps ?? null;
  const avgFps = runtime?.frame_source.avg_fps ?? null;
  // 15 polls at 2 s == ~30 s of sustained shortfall before offering advice.
  const showUnreachable =
    starvedPolls >= 15 && targetFps !== null && avgFps !== null && avgFps < targetFps;
  const suggestedFps = avgFps === null ? null : unreachableSuggestion(avgFps, supportedStep);

  return (
    <>
      <PanelSection>
        <StatusCard t={t} control={control} runtime={runtime} notice={notice} error={error} />
      </PanelSection>

      {showUnreachable && suggestedFps !== null && targetFps !== null ? (
        <PanelSection title={t.unreachableTitle}>
          <PanelSectionRow>
            <div style={blockStyle}>
              <div style={detailStyle}>
                {t.unreachableBody(targetFps.toFixed(0), avgFps!.toFixed(0))}
              </div>
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => applyFpsTarget(suggestedFps)}>
              {busy ? t.applying : t.unreachableAction(suggestedFps.toFixed(0))}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      ) : null}

      <PanelSection>
        <PanelSectionRow>
          <DropdownItem
            label={t.profileLabel}
            description={t.profileHints[profile]}
            // Inline puts the value in a narrow column beside the label, where
            // anything longer than a couple of characters is cut off mid-word.
            layout="below"
            rgOptions={PROFILE_ORDER.map((key) => ({ data: key, label: t.profiles[key] }))}
            selectedOption={profile}
            disabled={busy}
            onChange={(option) => applyProfile(option.data as Profile)}
          />
        </PanelSectionRow>
      </PanelSection>

      {profile === "off" ? null : (
        <PanelSection>
          <PanelSectionRow>
            <DropdownItem
              label={t.targetLabel}
              description={
                manualTarget
                  ? t.targetManualDescription(String(control?.fps_target_override.fps ?? ""))
                  : t.targetAutoDescription()
              }
              layout="below"
              rgOptions={targetOptions}
              selectedOption={selectedTarget}
              disabled={busy}
              onChange={(option) =>
                applyFpsTarget(option.data === "auto" ? null : Number(option.data))
              }
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label={t.diagnosticsToggle}
            description={showDiagnostics ? t.diagnosticsDescription : undefined}
            checked={showDiagnostics}
            onChange={setShowDiagnostics}
          />
        </PanelSectionRow>
      </PanelSection>

      {showDiagnostics ? (
        <>
          <PanelSection title={t.diagnosticsToggle}>
            <Diagnostics t={t} status={status} runtime={runtime} />
            <PanelSectionRow>
              <div style={blockStyle}>
                <div style={detailStyle}>
                  {t.diag.learning}: {mappedText(t.learningStates, learningKey(runtime?.learning))}
                </div>
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={load}>
                {busy ? t.applying : t.diag.refresh}
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={restore}>
                {busy ? t.applying : t.diag.restore}
              </ButtonItem>
            </PanelSectionRow>
          </PanelSection>

        </>
      ) : null}
    </>
  );
};

function learningKey(learning: LearningState | null | undefined): string {
  if (!learning) {
    return "learning";
  }
  if (learning.reusable_next_launch) {
    return "ready";
  }
  if (
    learning.skip_reason === "fps_target_unknown" ||
    learning.status === "waiting-for-fps-target"
  ) {
    return "needsTarget";
  }
  if (learning.status === "stopped" || learning.status === "view-data-only") {
    return "stopped";
  }
  return "learning";
}

export default definePlugin(() => ({
  name: "Game Power",
  titleView: <PluginTitle />,
  content: <GamePowerPanel />,
  icon: <FaGamepad />,
}));
