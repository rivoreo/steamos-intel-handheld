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

- PL1: requested TDP
- PL2: `max_w` by default, clamped so it is never below PL1

SteamOS Manager currently exposes one `TdpLimit` value, so PL2 is backend
policy. For the Core Ultra 7 258V profile, `max_w` is 37W, matching Intel's
Maximum Turbo Power value for this class. A 17W UI setting therefore maps to
17W PL1 and 37W PL2. Device profiles can lower PL2 with `--pl2-w` when platform
thermals require it.

The reason this is fixed-wattage policy rather than a PL1 multiplier is that
Intel publishes Base Power and Maximum Turbo Power values, not a PL2 ratio.
Linux RAPL exposes those controls as long-term and short-term constraints.

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
- Initial tested RAPL range: 5W to 37W
