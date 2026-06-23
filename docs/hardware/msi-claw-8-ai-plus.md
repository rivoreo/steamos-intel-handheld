# MSI Claw 8 AI+ A2VM

Initial target for this project.

## Observed device data

- Product family: MSI Claw 8 AI+
- DMI board name: `MS-1T52`
- CPU family: Intel Lunar Lake / 258V class
- SteamOS Manager package observed: `26.2.1-1`
- Tested RAPL domain: `/sys/class/powercap/intel-rapl:0`
- Tested TDP range: 5W to 37W

## Verified behavior

On the first test device, SteamOS Manager accepted a remote `TdpLimit1`
provider through `/etc/steamos-manager/remotes.d`. Setting 28W through
`steamosctl set-tdp-limit 28` updated:

- SteamOS Manager central `TdpLimit`
- remote provider `TdpLimit`
- Intel RAPL PL1

The test restored 30W afterward.

When the SteamOS UI TDP-limit toggle was enabled and set to 17W, the same path
reported:

- SteamOS Manager central `TdpLimit`: 17W
- remote provider `TdpLimit`: 17W
- RAPL PL1: 17W
- RAPL PL2: initially 21W in the prototype that used a `1.25x` heuristic

The SteamOS UI controls the single SteamOS Manager `TdpLimit`; this project maps
that to PL1. The formal project defaults PL2 to the profile `max_w` value
(37W for this processor class) to align with Intel's Maximum Turbo Power model,
with a `--pl2-w` override available for lower platform-specific burst limits.

## Boot note

After reboot, the service started and SteamOS Manager rediscovered the remote.
The service first read the persisted 30W state, then a SteamOS-side client set
TDP to 37W. The provider obeyed that request. This should be treated as a
SteamOS policy interaction, not as a state persistence failure.
