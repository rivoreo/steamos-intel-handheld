# Upstreaming Plan

The near-term repo should prove behavior outside SteamOS Manager. Upstreaming
should happen only after the interface and hardware assumptions are stable.

## Candidate upstream pieces

1. Device metadata for MSI Claw 8 AI+ and compatible Intel handhelds.
2. A generic Intel RAPL TDP backend.
3. Remote interface documentation improvements for SteamOS Manager.
4. Packaging conventions for third-party SteamOS Manager remotes.

## Evidence needed before a PR

- Reboot verification on at least one MSI Claw 8 AI+.
- Confirmation of the supported TDP range across AC and battery modes.
- Behavior notes for Steam client boot/login policy, especially when it sets
  TDP to the maximum value after the remote appears.
- Logs showing no SteamOS Manager startup deadlock when the remote starts after
  the user service is active.

## Non-goals for the first PR

- Replacing SteamOS Manager's internal TDP manager.
- Forcing a boot-time TDP policy that competes with the Steam client.
- Claiming support for every Intel handheld before collecting device data.
