# Charge Limit Decky Plugin

This Decky Loader plugin controls the MSI Claw 8 AI+ A2VM battery charge-limit
preset from SteamOS.

The plugin calls `steamos-intel-handheld-ec-control` for JSON status, preset
previews, and validated preset writes. It writes only EC byte `0xd7`, and only
for the `60`, `80`, and `100` percent presets confirmed by paired Windows and
SteamOS EC dumps.

The CLI fails closed unless DMI reports MSI `Claw 8 AI+ A2VM` on board
`MS-1T52`. Do not use the plugin on other devices until their EC mapping is
independently validated.

Validated values:

```text
60% stop / 50% restart   -> EC[0xd7] = 0xbc
80% stop / 70% restart   -> EC[0xd7] = 0xd0
100% stop / 90% restart  -> EC[0xd7] = 0xe4
```
