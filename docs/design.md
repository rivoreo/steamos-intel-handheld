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
- PL2: `min(PL1 + 2W, 32W, short_limit_max_w)`

SteamOS Manager currently exposes one `TdpLimit` value, so PL2 is backend
policy. For the Core Ultra 7 258V profile, Intel's published package-power
boundaries are 8W minimum guaranteed power, 17W base power, 30W maximum
guaranteed sustained power, and 37W maximum turbo power. The Claw 8 AI+
game-test points use 17W/19W and 30W/32W, so this project treats 37W as a
hardware ceiling rather than the default game PL2.

The reason this is fixed-delta policy rather than a PL1 multiplier is that the
tested handheld data points form a +2W curve. Linux RAPL exposes those controls
as named long-term and short-term constraints, so the backend resolves
`constraint_X_name` instead of assuming fixed constraint indices. The tested
kernel reports `long_term` `max_power_uw` as 17W even though 30W writes are
accepted, so PL1 follows the validated 8W to 30W handheld range. For PL2, a
non-zero short-term `max_power_uw` is still treated as a burst ceiling.

The persisted state is advisory. The service does not force a boot-time TDP by
default because SteamOS or the Steam client may apply its own policy after login.
The optional `--restore-on-start` flag exists for experiments and device
profiles that explicitly want that behavior.

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
