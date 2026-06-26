# EC Charge Manager Plugin Design

## Goal

Create the first framework for an MSI Claw 8 AI+ EC charge-limit manager that can
be surfaced through Decky Loader on SteamOS. The first version must be safe: it
can read and decode the likely MSI EC charge threshold, preview preset writes,
and show a Steam-side UI skeleton, but it must not write the unverified charge
control byte yet.

## Scope

- Add a Python EC charge-limit module with the upstream Linux `msi-ec` threshold
  encoding for the current leading candidate, EC offset `0xd7`.
- Add CLI commands that return JSON for status and preset preview so a plugin
  backend has a stable local API.
- Add a Decky plugin skeleton under `decky/steamos-intel-handheld-ec/` with a
  React panel and Python backend functions.
- Keep actual EC charge-limit writes disabled until paired 60/80/100 Windows
  dumps prove the Claw mapping.

## Architecture

The root-owned SteamOS support package remains the only layer allowed to touch
EC debugfs. Decky is only a frontend/backend adapter: it requests status and
previews through the local CLI now, and can later switch to a root D-Bus API
when writes become safe.

The EC charge module uses a deliberately small model:

- EC address `0xd7`
- start offset `0x8a`
- end offset `0x80`
- presets: 60, 80, 100
- write state: disabled with an explicit safety error

This makes the current hypothesis visible without normalizing unknown raw EC
writes into the product.

## UI Behavior

The Decky panel is a compact utility view:

- current decoded mode, raw EC byte, start threshold, stop threshold
- explanatory line for hysteresis, for example `80% mode restarts charging below 70%`
- three preset buttons: `60%`, `80%`, `100%`
- disabled write/apply state until hardware validation is complete
- visible loading and error states

## Testing

- Unit-test EC threshold encode/decode and preset previews.
- Unit-test the CLI JSON output with temporary EC debugfs and DMI fixtures.
- Asset-test Decky plugin metadata and backend/frontend contract strings.

## External References

- Decky Loader: <https://github.com/SteamDeckHomebrew/decky-loader>
- Decky plugin template: <https://github.com/SteamDeckHomebrew/decky-plugin-template>
- Linux `msi-ec`: <https://github.com/torvalds/linux/blob/master/drivers/platform/x86/msi-ec.c>
