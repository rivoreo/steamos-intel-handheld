# Game Power v2 Runtime Control Design

## Goal

Replace the Game Power Decky plugin's systemd runtime drop-in control path with
a project-owned runtime control API that the daemon reads live. The result must
keep the measured scheduler constants internal while allowing safe user-facing
mode changes from Decky.

## Scope

This v2 is intentionally convergent:

- implement safe mode control: `off`, `observe`, `automatic`;
- implement daemon live reload from a runtime JSON control file;
- update the Decky backend to call the project-owned control CLI;
- keep current measured balanced policy constants unchanged;
- verify locally and on the real handheld.

Out of scope for this v2:

- FPS-target closed-loop tuning,
- per-game cached profiles,
- direct thread affinity control,
- exposing expert low-level scheduler knobs,
- replacing the current TDP or SteamOS Manager DBus APIs.

## Control Contract

Add a new CLI entry point:

- `steamos-intel-handheld-game-power-control`

The CLI owns a runtime control file:

- `/run/steamos-intel-handheld/game-power-control.json`

File schema:

```json
{
  "schema_version": 1,
  "mode": "automatic",
  "source": "decky"
}
```

Valid public modes:

- `automatic` maps to internal `gpu-priority`,
- `observe` maps to internal `observe`,
- `off` maps to internal `off`.

The CLI must reject any other mode. It must not accept P-core/E-core
frequencies, thresholds, uclamp values, CPUWeight, PL2/Tau, affinity masks, or
raw cgroup paths.

Subcommands:

- `status --json`: report configured mode, effective internal mode, whether an
  override file exists, the policy label, and supported public modes.
- `set-mode MODE --source decky --json`: atomically write the runtime control
  file.
- `restore-defaults --json`: remove the runtime control file.

## Daemon Behavior

`steamos-intel-handheld-power-control` keeps its packaged default game-power
configuration. On each governor iteration, it reads the runtime control file
and overlays only the game-power mode. All other policy constants continue to
come from the packaged service command line.

When the effective mode changes, the governor must:

- restore any active CPU policy snapshot before switching mode,
- reset controller hysteresis,
- clear prior write-failure state,
- continue without restarting systemd.

If the effective mode is `off`, the governor should avoid expensive foreground
sampling and simply sleep for the configured poll interval.

## Decky Backend

The Decky backend must remove the v1 drop-in writer. It should call only:

- `steamos-intel-handheld-game-power-control status --json`,
- `steamos-intel-handheld-game-power-control set-mode MODE --source decky --json`,
- `steamos-intel-handheld-game-power-control restore-defaults --json`,
- `steamos-intel-handheld-game-power --mode observe ... --output-format jsonl`
  for a short diagnostic sample.

It may still call `systemctl show` read-only to report service state. It must
not call `systemctl restart`, `systemctl daemon-reload`, `tee`, `rm`, or write
any systemd unit/drop-in path.

## Acceptance Criteria

- Decky source and backend contain no `70-game-power-decky.conf`, `ExecStart=`,
  systemd drop-in write, or service restart path.
- New CLI is packaged in `pyproject.toml`, development installer, and release
  artifact checks.
- Unit tests prove mode validation, atomic runtime file writes, restore,
  default fallback, and daemon live reload behavior.
- The existing Game Power Decky UI still exposes only safe intent controls.
- Required local harness passes.
- Real handheld install succeeds; backend `set_mode("observe")`,
  `restore_defaults()`, and status checks work without runtime systemd drop-in
  residue.
