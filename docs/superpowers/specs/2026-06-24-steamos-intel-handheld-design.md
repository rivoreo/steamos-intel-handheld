# SteamOS Intel Handheld Design

## Objective

Create a public `rivoreo/steamos-intel-handheld` project for SteamOS support on
MSI Claw 8 AI+ and future Intel handhelds.

## Initial feature

The first feature is a SteamOS Manager remote TDP provider backed by Intel RAPL.
It exposes:

- bus name: `org.rivoreo.SteamOSManager.PowerControl`
- object path: `/org/rivoreo/SteamOSManager/PowerControl`
- interface: `com.steampowered.SteamOSManager1.TdpLimit1`

## Requirements

- Keep hardware access behind a testable backend.
- Keep D-Bus imports out of backend unit tests.
- Require TDD evidence for production behavior changes through the AI
  development harness and pull request template.
- Provide a direct install script for root SSH targets.
- Provide a verification script that sets a test wattage and restores a safe
  wattage.
- Include docs for the first MSI Claw 8 AI+ target.
- Document the PL1/PL2 mapping: SteamOS UI TDP maps to RAPL long-term power,
  while the default short-term power uses the device profile maximum turbo
  wattage.
- Include a packaging path for future Arch/SteamOS packaging.
- Use `JohnnySun <bmy001@gmail.com>` as git author metadata.

## Design decision

The repo implements an external remote provider rather than patching SteamOS
Manager directly. This keeps the local support layer small and provides a
cleaner upstream path once behavior is validated across devices.
