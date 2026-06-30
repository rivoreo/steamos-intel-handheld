# Steam Beta Display Settings Regression, 2026-06-27

## Summary

Steam beta client build `1782517397` crashes when opening the SteamOS
`Settings > Display` page on the MSI Claw 8 AI+ test device. The visible error
is:

```text
TypeError: Cannot read properties of undefined (reading 'width')
```

The crash is separate from the gamescope known-display gap. Installing the
local gamescope profile for the `CSW` `PN8007QB1-2` panel makes gamescope
identify the panel and exposes the full `48-120Hz` refresh range, but the beta
Steam UI still crashes because `DisplayManager.GetState()` omits
`game_resolution_override_default`.

## Device

- Product: MSI Claw 8 AI+ A2VM
- Display connector: `eDP-1`
- Panel EDID: vendor `CSW`, model `PN8007QB1-2`, product `0x0801`
- Panel resolution: `1920x1200`
- SteamOS: `3.8.11`, build `20260620.1`
- Steam stable client build verified usable: `1782533657`
- Steam beta client build reproducing the crash: `1782517397`

## Observed Display State

After installing the local gamescope profile and restarting the gamescope
session, `gamescopectl` reports:

```text
Display Make: China Star Optoelectronics Technology Co., Ltd
Display Model: PN8007QB1-2
Display Flags: 0x5
ValidRefreshRates: 48, 49, 50, ..., 120
```

The gamescope journal also reports:

```text
drm: Got known display: msi_claw_8_ai_plus_lcd (MSI Claw 8 AI+ LCD)
drm: selecting mode 1920x1200@120Hz
```

## Steam Beta Failure

On Steam beta build `1782517397`, calling
`SteamClient.System.DisplayManager.GetState()` from the shared Steam UI
JavaScript context returns:

```text
field 1 displays[0].current_mode = 1920x1200@120
field 4 game_resolution_override_native = 1920x1200
field 5 game_resolution_override_default = <missing>
```

Rechecking the live beta client through the `SharedJSContext` DevTools target
decoded the top-level protobuf as fields `1`, `2`, `3`, and `4` only. Field `4`
decoded as:

```text
game_resolution_override_native.width = 1920
game_resolution_override_native.height = 1200
```

No top-level field `5` was present in the reply.

The beta Steam UI display settings code reads both fields without guarding for
missing data. The crash path observed in the rendered error page is:

```text
TypeError: Cannot read properties of undefined (reading 'width')
    at j (https://steamloopback.host/chunk~2dcc5aaf7.js?...:1:8081620)
```

The minified `j()` function reads `l.data.width` where `l` is the selector for
`game_resolution_override_default`.

## Native/SteamUI Package Evidence

The downloaded Steam beta package is internally consistent; this is not a
partial or corrupt Steam update. The beta `steamclient.so` and SteamUI bundle
both know about the new fields:

```text
CMsgSystemDisplayManagerState
field 4 game_resolution_override_native
field 5 game_resolution_override_default
```

The stable SteamUI bundle does not read these fields. The beta bundle adds a
new Display settings selector for `game_resolution_override_default` and then
uses `l.data.width` and `l.data.height` without checking whether the field is
present.

The beta native package also contains the relevant source-path strings:

```text
/data/src/clientdll/systemdisplaymanager.cpp
GetGameResolutionOverride: override: '%s'
Using maximum game resolution: screen resolution: %dx%d
ResolutionOverrideInternalDisplay
DeckResolutionOverride
/data/src/clientdll/systemdisplaymanager_wayland.cpp
OnOutputMode
```

This separates two data paths:

- Wayland/display mode data is handled by `systemdisplaymanager_wayland.cpp`.
  On this device it is present: `GetState()` reports the current mode as
  `1920x1200@120`, and field 4 reports native resolution `1920x1200`.
- The missing field 5 is produced by the generic game-resolution override
  policy in `systemdisplaymanager.cpp`. That path is not supplied by the
  gamescope Lua display profile.

The beta `systemdisplaymanager.txt` log shows that this policy can still
resolve the device's current `Default` value:

```text
GetGameResolutionOverride: override: 'Default'
Using maximum game resolution: screen resolution: 1920x1200
```

So the failure is not that `Default` cannot be computed. The failure is that
the computed default is not serialized into
`CMsgSystemDisplayManagerState.game_resolution_override_default`.

## Gamescope Source Evidence

The upstream gamescope `gamescope-control` protocol version 6 currently emits
`active_display_info` with only:

```text
connector_name
display_make
display_model
display_flags
valid_refresh_rates
```

It does not include a default game resolution, native width/height, or a policy
field for Steam's `game_resolution_override_default`. The gamescope known
display Lua schema used by the profile can provide dynamic refresh rates,
timings, HDR, and colorimetry metadata, but there is no existing profile key
for Steam's default game-resolution policy.

## Setting Experiment

Using the same SteamUI setting API that the Display settings page uses, the
global setting was changed and restored:

```text
gamescope_game_resolution_global = Default
gamescope_game_resolution_global = Native
gamescope_game_resolution_global = 1920x1200
gamescope_game_resolution_global = Default
```

For all four reads, `DisplayManager.GetState()` still returned:

```text
field 4 game_resolution_override_native = 1920x1200
field 5 game_resolution_override_default = <missing>
```

So the missing field is not caused by an invalid
`gamescope_game_resolution_global` setting.

## Device Identity

Steam beta identifies the test device as a SteamOS/gamescope handheld:

```text
sOSName = SteamOS Holo
sOSVariantId = steamdeck
eGamingDeviceType = 541
sProductVendor = Micro-Star International Co., Ltd.
bIsDeckOled = false
GetRegisteredSteamDeck = false
```

The device is not a registered Valve Steam Deck, but it is on the SteamOS
`steamdeck` variant path and Steam is launched with the Steam Deck/gamepad UI
flags. The bug is therefore narrower than "not in gamescope" and wider than a
single corrupted install: beta native DisplayManager state omits a submessage
that the beta UI assumes is always present.

## Stable Behavior

Steam stable build `1782533657` also omits
`game_resolution_override_default` from `DisplayManager.GetState()` on this
device, but its Display settings UI does not unconditionally read the missing
field and the page renders successfully.

Stable's Display settings UI still builds the `Highest game resolution` option
list from a fixed list:

```text
Default
Native
3840x2160
2560x1600
...
1920x1200
...
```

The beta UI changed this path. It now uses
`game_resolution_override_native` and `game_resolution_override_default` from
`DisplayManager.GetState()` to generate the option list dynamically and mark
the default resolution as recommended. That is why the same missing native
state field is harmless on stable but fatal on beta.

Stable renders the page with `Highest game resolution` set to `Default`.
Steam's native `systemdisplaymanager.txt` log shows what `Default` resolves to
when the native override path is invoked:

```text
GetGameResolutionOverride: override: 'Default'
Using maximum game resolution: screen resolution: 1920x1200
```

So the default value for this device is `1920x1200`; the beta regression is
that the new UI expects that value to be present in `GetState()` field 5, but
the native DisplayManager reply does not include it.

## Native-Panel Override Check

The local gamescope override does not set Steam's game-resolution policy. The
deployed files only do two things:

- put `/opt/steamos-intel-handheld/bin` before `/usr/bin` for
  `gamescope-session.service`;
- wrap `gamescope` so the original SteamOS `-w`/`-h` arguments are rewritten to
  the connected internal panel's first DRM mode, currently `1920x1200`;
- run `gamescopectl composite_force 1` after the gamescope session starts.

The deployed wrapper and runtime environment do not contain
`gamescope_game_resolution_global`, `ResolutionOverrideInternalDisplay`, or
`DeckResolutionOverride`. The live gamescope environment also contains no
game-resolution override variable.

This means the local override can change the screen/native resolution that
Steam sees, but it does not provide or remove
`game_resolution_override_default`. On the affected beta build, Steam already
sees the correct current mode and native value:

```text
OnOutputMode: 1920 x 1200 @ 120.002
game_resolution_override_native: 1920x1200
```

The missing value is still specifically field 5 in the Steam native state
reply, not a missing gamescope launch argument.

## Current Assessment

There are two distinct issues:

1. gamescope did not know the MSI Claw 8 AI+ `PN8007QB1-2` panel. The local
   workaround fixes this by adding a known-display Lua profile.
2. Steam beta build `1782517397` does not tolerate a missing
   `game_resolution_override_default` in `DisplayManager.GetState()`. This is
   a Steam client beta regression in the native DisplayManager/UI contract.
   The value exists as a native policy result, but is absent from the state
   object consumed by the new UI. It is not directly fixed by the gamescope
   display profile.

The expected Steam-side fix is either:

- populate `game_resolution_override_default` with the screen-resolution default
  when `gamescope_game_resolution_global` is `Default`, or
- make the beta Display settings UI fall back to the current/native display
  resolution when the field is absent.

A gamescope-side protocol extension could expose more display-resolution
metadata through `gamescope-control`, but the current Steam beta client does
not consume such a field. The immediate beta crash needs a Steam native/UI
contract fix.

## Root Cause

The missing feature is not the gamescope window size. On the beta build, the
device already reports:

```text
gamescope launch args include: -w 1920 -h 1200
DisplayManager display current mode: 1920x1200@120
DisplayManager game_resolution_override_native: 1920x1200
```

The missing feature is Steam's default game-resolution policy value:

```text
DisplayManager game_resolution_override_default: <missing>
```

The Steam beta UI added a new dependency on that field in both the global
Display page and App Properties. It calls the selector for
`game_resolution_override_default` and immediately dereferences
`data.width/data.height`. On this third-party SteamOS device, the native
DisplayManager omits the submessage, so the UI crashes before rendering the
setting.

The likely reason Steam Deck itself does not reproduce this is that a Valve
Steam Deck is in Steam's hardware-default configuration path, which can provide
`DeckResolutionOverride` / default resolution policy. This MSI Claw test device
is running the SteamOS `steamdeck` variant and gamescope UI, but it is not Valve
Steam hardware:

```text
sProductVendor = Micro-Star International Co., Ltd.
GetRegisteredSteamDeck = false
```

That leaves the beta UI on a SteamOS/gamescope path while the native
DisplayManager does not populate the new default-resolution submessage. This is
why a gamescope profile fixes panel identification and native mode reporting
but does not fix the beta Display page crash.

## Upstream Reports

- gamescope panel profile PR: <https://github.com/ValveSoftware/gamescope/pull/2236>
- Steam Client beta regression issue: <https://github.com/ValveSoftware/steam-for-linux/issues/13349>
