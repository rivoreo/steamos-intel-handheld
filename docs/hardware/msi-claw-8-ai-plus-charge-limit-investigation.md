# MSI Claw 8 AI+ charge-limit investigation

This note preserves the live SteamOS observations from 2026-06-26 before the
device was taken offline. It intentionally separates known raw values from
unproven EC field meanings.

## Device context

- Device: MSI Claw 8 AI+ A2VM
- Board: MS-1T52
- BIOS: E1T52IMS.112, 2025-12-04
- EC firmware string at EC offset `0xa0`:
  `1T52EMS1.1091204202509:10:47`
- SteamOS: 3.8.11, build `20260620.1`
- Kernel: `6.16.12-valve24-1-neptune-616-gc748040e4712`
- SteamOS Manager: `26.2.1-1`
- User-reported Windows policy: MSI Windows utility charge limit set to 80%.

## User-observed behavior

1. The 80% battery limit was set in the MSI Windows utility.
2. When the device is returned to Windows from the bad SteamOS state, Windows
   appears to rewrite or refresh EC state and charging resumes normally at about
   60 W.
3. After booting from Windows into SteamOS, charging initially works until the
   battery reaches 80%, then stops as expected for an 80% cap.
4. After reaching that cap, later SteamOS sessions can remain in bypass /
   pending-charge even after the battery has discharged to about 75%.
5. The user has previously unplugged and replugged at 77%-78% while charging to
   80%, and charging continued. The sticky bad state appears after hitting 80%.

## Known EC writes from this project

The installed `steamos-intel-handheld-power-control.service` was running with:

```text
--apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750
```

The service only writes these MSI Claw EC offsets:

- `0x50`: TDP PL1 in watts.
- `0x51`: TDP PL2 in watts.
- `0xd2`: MSI shift/user-scenario byte, `0xc1` for <=17 W and `0xc4` for >17 W.

No current repo code writes battery charge limit, charge-start threshold,
charge-stop threshold, charger enable, or AC adapter state.

## Snapshot A: charger attached, bypass / pending-charge

Time: 2026-06-26 10:20 Asia/Taipei.

Power supply state:

```text
ADP1 online=1
BAT1 status=Not charging
BAT1 capacity=76
BAT1 charge_now=5200000
BAT1 charge_full=6874000
BAT1 charge_full_design=6560000
BAT1 current_now=0
BAT1 voltage_now=12474000
UPower state=pending-charge
UPower energy-rate=0 W
```

Important ratios:

```text
charge_now / charge_full        = 5200000 / 6874000 = 75.65%
charge_now / charge_full_design = 5200000 / 6560000 = 79.27%
```

EC dump:

```text
000000 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000010 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000020 00 00 00 00 00 00 00 00 0a 05 00 00 08 2c 0b 09
000030 02 05 00 0d 01 00 50 81 a0 19 5a 2d 90 02 c0 00
000040 30 1c 4c 00 da 1a 4e fb 44 14 56 30 db 0b 26 34
000050 0c 0e 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000060 00 00 00 00 00 00 00 00 35 00 32 3c 46 50 58 58
000070 64 28 00 28 31 3a 43 4b 4b 00 2c 36 40 4a 52 52
000080 00 00 32 3c 46 50 58 58 00 00 00 28 31 3a 43 4b
000090 4b 00 2c 36 40 4a 52 52 02 00 00 00 00 00 30 00
0000a0 31 54 35 32 45 4d 53 31 2e 31 30 39 31 32 30 34
0000b0 32 30 32 35 30 39 3a 31 30 3a 34 37 00 00 00 00
0000c0 00 00 01 31 00 00 00 00 00 c3 00 c4 00 00 00 00
0000d0 00 00 c1 80 00 00 05 d0 00 01 00 00 00 0b 00 00
0000e0 e2 00 00 da 1a 00 00 00 00 00 00 00 00 83 00 00
0000f0 00 00 70 00 30 7f 05 2f 64 00 00 00 00 00 00 00
000100
```

Key EC values:

```text
ec[0x42]=0x4c/76
ec[0x50]=0x0c/12
ec[0x51]=0x0e/14
ec[0x6d]=0x50/80
ec[0x85]=0x50/80
ec[0x9e]=0x30/48
ec[0xd2]=0xc1/193
ec[0xd3]=0x80/128
ec[0xd7]=0xd0/208
ec[0xf4]=0x30/48
ec[0xf8]=0x64/100
```

## Snapshot B: charger unplugged, discharging

Time: 2026-06-26 10:29 Asia/Taipei.

Power supply state:

```text
ADP1 online=0
BAT1 status=Discharging
BAT1 capacity=75
BAT1 charge_now=5130000
BAT1 charge_full=6874000
BAT1 charge_full_design=6560000
BAT1 current_now=332000
BAT1 voltage_now=12408000
```

Change from Snapshot A:

```text
charge_now:         5200000 -> 5130000
charge_full:        6874000 -> 6874000
charge_full_design: 6560000 -> 6560000
capacity:           76 -> 75
```

EC dump:

```text
000000 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000010 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000020 00 00 00 00 00 00 00 00 0a 05 00 00 08 2c 0b 09
000030 02 05 00 0d 01 00 50 81 a0 19 5a 2d 90 02 c0 00
000040 30 1c 4b 00 da 1a ca fc 07 14 53 30 e3 0b 26 34
000050 0c 0e 00 00 00 00 00 00 00 00 00 00 00 00 00 00
000060 00 00 00 00 00 00 00 00 34 00 32 3c 46 50 58 58
000070 64 28 00 28 31 3a 43 4b 4b 00 2c 36 40 4a 52 52
000080 00 00 32 3c 46 50 58 58 00 00 00 28 31 3a 43 4b
000090 4b 00 2c 36 40 4a 52 52 02 00 00 00 00 00 2f 00
0000a0 31 54 35 32 45 4d 53 31 2e 31 30 39 31 32 30 34
0000b0 32 30 32 35 30 39 3a 31 30 3a 34 37 00 00 00 00
0000c0 00 00 01 31 00 00 00 00 00 c8 00 c7 00 00 00 00
0000d0 00 00 c1 80 00 00 05 d0 00 01 00 00 00 07 00 00
0000e0 e2 00 00 da 1a 00 00 00 00 00 00 00 00 83 00 00
0000f0 00 00 70 00 2f 7f 05 2c 64 00 00 00 00 00 00 00
000100
```

Key EC values:

```text
ec[0x42]=0x4b/75
ec[0x50]=0x0c/12
ec[0x51]=0x0e/14
ec[0x6d]=0x50/80
ec[0x85]=0x50/80
ec[0x9e]=0x2f/47
ec[0xd2]=0xc1/193
ec[0xd3]=0x80/128
ec[0xd7]=0xd0/208
ec[0xf4]=0x2f/47
ec[0xf8]=0x64/100
```

## Snapshot C: charger reattached, still bypass / pending-charge

Times: 2026-06-26 10:34:46 and 10:35:19 Asia/Taipei.

Power supply state:

```text
ADP1 online=1
BAT1 status=Not charging
BAT1 capacity=74
BAT1 charge_now=5106000
BAT1 charge_full=6874000
BAT1 charge_full_design=6560000
BAT1 current_now=0
BAT1 voltage_now=12425000
UPower state=pending-charge
UPower energy-rate=0 W
UPower percentage=74%
```

The second sample 33 seconds later was unchanged at `charge_now=5106000` and
`current_now=0`.

Key EC values at 10:35:19:

```text
ec[0x42]=0x4b/75
ec[0x50]=0x0c/12
ec[0x51]=0x0e/14
ec[0x68]=0x31/48
ec[0x6d]=0x50/80
ec[0x85]=0x50/80
ec[0x9e]=0x2c/44
ec[0xd2]=0xc1/193
ec[0xd3]=0x80/128
ec[0xd7]=not captured in the short sample
ec[0xf4]=0x2d/45
ec[0xf5]=0x7f/127
ec[0xf6]=0x05/5
ec[0xf7]=0x2a/42
ec[0xf8]=0x64/100
```

## Working interpretation

Known:

- SteamOS sees the AC adapter as online when the charger is attached.
- In the bad state, Linux reports `Not charging` and UPower reports
  `pending-charge`, with zero battery current and zero UPower energy rate.
- Linux does not expose standard charge-control sysfs attributes on this
  device:
  `charge_control_start_threshold`,
  `charge_control_end_threshold`, or `charge_behaviour`.
- `vpower` logged that it could not find a suitable MaxChargeLevel file and
  assumed `MaxChargeLevel=100%`.
- EC offset `0x42` tracks a battery percentage-like value: it changed from
  `76` to `75` when the battery discharged.
- EC offsets `0x6d` and `0x85` held decimal `80` across the observed states.
- EC offset `0xd3` held `0x80` across the observed states, but that is decimal
  `128`, so it must not be casually treated as a decimal 80% limit.
- EC offset `0xd7` held `0xd0` in the full dumps. This was not in the first
  key-byte list, but it is probably important.

Not yet proven:

- Claw-specific proof that `0xd7` is the MSI Windows utility's charge-control
  value. The external Linux MSI EC evidence below makes it the leading
  candidate.
- Whether `0x6d`, `0x85`, `0xd3`, or nearby bytes are also involved in charge
  control or merely unrelated EC tables/state.

The strongest current clue is behavioral rather than byte-level: Windows can
recover the state and restore normal 60 W charging, while SteamOS can initially
charge to 80% after Windows has refreshed EC state but later remains in bypass
after reaching the cap and discharging to about 75%.

## External Linux MSI EC references

Upstream Linux has a mainline `msi-ec` driver:

- Source: <https://github.com/torvalds/linux/blob/master/drivers/platform/x86/msi-ec.c>
- Header: <https://github.com/torvalds/linux/blob/master/drivers/platform/x86/msi-ec.h>

That driver says it exports battery charge thresholds to userspace. In its
known configurations, charge control uses one EC address with encoded values:

```text
offset_start = 0x8a
offset_end   = 0x80
range_min    = 0x8a
range_max    = 0xe4
```

The driver's read path decodes thresholds from the same EC byte:

```text
start threshold = ec_value - 0x8a
end threshold   = ec_value - 0x80
```

So an EC value of `0xd0` means:

```text
end threshold   = 0xd0 - 0x80 = 0x50 = 80%
start threshold = 0xd0 - 0x8a = 0x46 = 70%
```

This is a strong match for the observed `ec[0xd7]=0xd0`. Several mainline MSI
EC configurations use `0xd7` as the `charge_control.address`, and several also
use `0xd2` as the shift-mode address. The Claw 8 AI+ firmware string
`1T52EMS1.109` is not currently in the mainline driver's allowlist, so SteamOS
does not expose these thresholds through standard power_supply sysfs on this
device.

The out-of-tree `BeardOverflow/msi-ec` project documents the user-visible
threshold semantics:

- Source: <https://github.com/BeardOverflow/msi-ec>
- README: <https://github.com/BeardOverflow/msi-ec/blob/main/README.md>
- Source file: <https://github.com/BeardOverflow/msi-ec/blob/main/msi-ec.c>

Its README says:

- `charge_control_start_threshold` is the level below which charging begins.
- `charge_control_end_threshold` is the level above which charging stops.
- MSI's medium battery mode maps to start `70` and end `80`.

This explains the user-observed stuck state without requiring an additional
mystery latch:

1. Windows sets the MSI medium/80% policy into EC.
2. SteamOS can charge until it reaches the end threshold, 80%.
3. After EC enters the stopped/pending-charge state, it should not restart until
   battery state falls below the start threshold, about 70%.
4. At Linux `74%` and EC-like `75%`, the device is still above a 70% restart
   threshold, so bypass / `pending-charge` is expected.

This also explains the user report that unplugging/replugging at 77%-78% while
charging still kept charging: before the end threshold is reached, the charge
cycle has not latched into the stopped state. Once it reaches 80%, the EC
requires discharge below the lower start threshold before starting a new charge
cycle.

Working hypothesis after external research:

```text
ec[0xd7] = 0xd0
end threshold   = 80%
start threshold = 70%
```

This was the best explanation before paired Windows dumps were collected.
Because `1T52EMS1.109` is not an upstream-supported MSI EC firmware profile,
the mapping still needed Claw-specific validation.

## Validation experiment

Paired dumps were collected after changing only the Windows MSI battery
setting:

Observed values:

```text
60% end / 50% start   -> ec[0xd7] = 0xbc
80% end / 70% start   -> ec[0xd7] = 0xd0
100% end / 90% start  -> ec[0xd7] = 0xe4
```

The focused EC bytes used by existing power/TDP control remained stable across
the 60% and 100% charge-limit changes:

```text
0x50: unchanged
0x51: unchanged
0xd2: unchanged
0xd7: charge-limit preset byte
```

Evidence files:

- `docs/hardware/ec-dumps/20260627-002908-steamos-before-windows-60.txt`
- `docs/hardware/ec-dumps/20260627-003427-steamos-after-windows-60.txt`
- `docs/hardware/ec-dumps/20260627-003427-steamos-after-windows-60-vs-before.diff.txt`
- `docs/hardware/ec-dumps/20260627-004115-steamos-after-windows-100.txt`
- `docs/hardware/ec-dumps/20260627-004115-steamos-after-windows-100-vs-before.diff.txt`

Conclusion: for the MSI Claw 8 AI+ firmware observed here, the SteamOS control
path may write only EC offset `0xd7` and only the validated preset values
`0xbc`, `0xd0`, and `0xe4`.

Also collect two state-transition dumps:

1. Good SteamOS charging below the cap.
2. Immediately after reaching 80% and stopping.
3. After discharging to 75% and reattaching the charger while still stuck in
   bypass.

Do not write unknown EC battery-control offsets from Linux. The validated write
surface is limited to `0xd7` with the known 60/80/100 preset values.
