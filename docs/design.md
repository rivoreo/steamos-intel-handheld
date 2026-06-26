# Design

## Goal

Provide a small SteamOS support layer for Intel handhelds without patching
SteamOS Manager in place. The first feature is TDP control for MSI Claw 8 AI+
through SteamOS Manager's remote interface support.

## Architecture

`steamos-intel-handheld-power-control` runs as root on the system bus and owns
`org.rivoreo.SteamOSManager.PowerControl`. It exports the SteamOS Manager
`TdpLimit1` interface at `/org/rivoreo/SteamOSManager/PowerControl`.

SteamOS Manager discovers that provider through a static config file in
`/etc/steamos-manager/remotes.d`. The service intentionally waits for the deck
user's `steamos-manager` user service before owning the bus name. This avoids a
startup ordering problem where SteamOS Manager can block while trying to proxy a
remote before its internal TDP manager task is ready.

The backend writes Intel RAPL:

- PL1: requested TDP clamped to the handheld sustained range, 8W to 30W
- PL2: profile-aware backend policy derived from PL1, current power source,
  and selected TDP policy mode
- Tau: short-term RAPL time window from policy when the kernel exposes writable
  `constraint_X_time_window_us`

SteamOS Manager currently exposes one `TdpLimit` value, so PL2 is backend
policy. For the Core Ultra 7 258V profile, Intel's published package-power
boundaries are 8W minimum guaranteed power, 17W base power, 30W maximum
guaranteed sustained power, and 37W maximum turbo power. This project treats
37W as the AC Performance ceiling, not as the default battery PL2.

The default `--tdp-policy auto` resolves to Battery Max-Q on battery, AC
Performance on AC power, and Battery Max-Q when the power source is unknown.
Battery Max-Q uses ceiling-rounded ratio steps at low and mid slider values,
maps 17W and 18W PL1 to 25W PL2 with a short 5s Tau, and keeps 30W PL1 at 35W
PL2 with an 8s Tau. AC Performance maps 9W through 16W PL1 to 25W PL2 with a
10s Tau, then maps PL1 values of 17W and higher to 37W PL2 with a 28s Tau.
Battery Low Power and AC Quiet exist as explicit backend policy modes, but the
current SteamOS UI does not expose a separate profile signal for them.

Linux RAPL exposes power controls as named long-term and short-term
constraints, so the backend resolves `constraint_X_name` instead of assuming
fixed constraint indices. The tested kernel reports `long_term` `max_power_uw`
as 17W even though 30W writes are accepted, so PL1 follows the validated 8W to
30W handheld range. For PL2, a non-zero short-term `max_power_uw` is still
treated as a burst ceiling. If a short-term time-window file is missing or not
writable, Tau is skipped and the PL1/PL2 power-limit write remains the
authoritative operation. The PL2 ceiling is a hard cap: when PL1 itself reaches
that ceiling, the backend keeps PL2 at the ceiling instead of trying to maintain
the normal `PL2 >= PL1 + 1W` headroom.

The persisted state is advisory. The service does not force a boot-time TDP by
default because SteamOS or the Steam client may apply its own policy after login.
The optional `--restore-on-start` flag exists for experiments and device
profiles that explicitly want that behavior. On service startup without
`--restore-on-start`, the backend may still reapply the current PL2/Tau envelope
when the persisted state already matches the current long-term RAPL PL1; this
converges policy after service restarts without forcing an old PL1 back onto the
system.

## Boundaries

- Hardware access is isolated in `TdpBackend`.
- D-Bus code is loaded only inside `serve()` so unit tests do not need D-Bus.
- Device install and verification are shell harnesses under `scripts/`.
- Packaging files are drafts until the service layout is validated on more
  devices.

## Known first-device facts

- Device: MSI Claw 8 AI+ A2VM
- Board: MS-1T52
- SteamOS: 3.8.11 generation
- Kernel family: Valve Neptune 6.16
- SteamOS UI TDP range used by this project: 8W to 30W
- RAPL short-term hardware ceiling used by this project: 37W
