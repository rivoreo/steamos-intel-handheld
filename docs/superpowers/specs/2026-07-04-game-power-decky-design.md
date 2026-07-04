# Game Power Decky Plugin Design

## Goal

Build a separate Decky Loader plugin for the game-power governor. The plugin
is an intent and visibility surface for the scheduler, not a raw tuning panel.

## Product Boundary

The plugin must live under its own package directory:

- `decky/steamos-intel-handheld-game-power`

It must not be merged into the existing charge-limit plugin. Charge limiting
and game-power scheduling have different risk models, different copy, and
different expected controls.

## User Controls

Expose only high-level controls:

- governor mode: off, observe, automatic,
- current service status and foreground game state,
- a short live sample for package/core/uncore watts and active action,
- restore packaged defaults,
- diagnostics refresh.

Do not expose scientific policy internals as user controls:

- P-core max frequency,
- E-core max frequency,
- core-share thresholds,
- hysteresis sample counts,
- uclamp values,
- cgroup CPUWeight values,
- RAPL PL2 or Tau,
- CPU affinity masks.

Those values are measured policy constants. Users may choose intent, but they
must not be able to invalidate the measured basis of the policy from the Decky
UI.

## Architecture

Decky frontend calls a plugin-local Python backend. The backend shells out only
to project-owned system tools and systemd. Privileged writes remain inside a
small allowlisted runtime override boundary:

```text
Decky frontend
  -> Decky Python backend
    -> systemctl / steamos-intel-handheld-game-power
      -> steamos-intel-handheld-power-control.service
```

The runtime override may change the game-power mode, but all automatic mode
policy constants remain hardcoded to the measured balanced policy:

- mode `automatic` maps to `--game-power-mode gpu-priority`,
- CPU cap remains enabled,
- P-core/E-core caps and thresholds stay internal.

The backend may write only this runtime file:

- `/run/systemd/system/steamos-intel-handheld-power-control.service.d/70-game-power-decky.conf`

It must not write `/etc`, CPUFreq, RAPL, cgroup, uclamp, affinity, or EC state.
The override is runtime-only: it disappears on reboot. Restore removes only the
plugin-owned runtime drop-in, reloads systemd, and restarts the service. If the
plugin is removed without restore, the current boot may keep the runtime
drop-in until reboot, but packaged defaults remain intact and return after
reboot or manual restore.

The drop-in necessarily repeats the service command line for v1 because the
current daemon does not yet expose a live configuration API. This is acceptable
only under tests that ensure the command uses the measured balanced constants
and the UI exposes no raw controls. A future daemon API should replace the
drop-in path once live reconfiguration exists.

## Backend Contract

`get_status()` returns:

- `service.active_state`,
- `service.sub_state`,
- `service.mode`,
- `service.override_active`,
- `service.policy_label`.

`sample_once()` runs a short observe sample and returns:

- `appid`,
- `action`,
- `reason`,
- `package_w`,
- `core_w`,
- `uncore_w`,
- `pl1_w`,
- `render_busy`.

`set_mode(mode)` accepts only `off`, `observe`, or `automatic`, writes the
runtime drop-in, reloads systemd, restarts the service, and returns the selected
mode plus the policy label.

`restore_defaults()` removes only the plugin-owned runtime drop-in, reloads
systemd, restarts the service, and returns `restored: true`.

## UI

Use a compact Decky quick-access layout:

- top status block: mode, service state, foreground AppID/action,
- live metrics block: package/core/uncore watts,
- controls block: Off, Observe, Automatic buttons,
- maintenance block: restore defaults and refresh diagnostics,
- safety note: automatic mode uses measured policy constants.

The UI should be dark, dense, and readable. It should use standard Decky
components and `react-icons`, with no decorative visuals and no nested cards.
Traditional Chinese and English copy are required.

Screen states:

- loading: show a concise status row while the backend call is in flight,
- ready: show service state, mode, policy label, and latest sample if present,
- empty: show `No foreground game sample` when observe returns no AppID,
- applying: disable mode buttons while mode apply or restore is running,
- error: show a readable error line with `role="alert"` semantics where Decky
  components allow it.

## Acceptance Criteria

- A new plugin package exists independently from the charge-limit plugin.
- The development installer and Arch package install both plugins.
- The new plugin backend exposes status, sample, mode apply, and restore calls.
- The frontend contains no controls or labels for P-core/E-core frequency,
  thresholds, uclamp, CPUWeight, PL2/Tau, or affinity masks.
- Runtime writes are limited to the plugin-owned `/run/systemd` drop-in path.
- Tests verify the backend command boundary and the absence of unsafe UI knobs.
- Local required harness passes after implementation.
