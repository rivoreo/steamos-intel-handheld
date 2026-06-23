# AI Development Harness

This repository is arranged so AI agents can make changes with tight feedback.

## Local loop

Use hardware-free tests first:

```bash
PYTHONPATH=src python3 -m pytest
PYTHONPATH=src python3 -m compileall src
```

The tests build fake RAPL sysfs trees under pytest temporary directories, so no
root privileges or SteamOS host is needed for backend behavior.

## Device loop

Use the scripts against a root SSH target:

```bash
scripts/collect-device-info.sh root@192.168.128.214
scripts/install-on-device.sh root@192.168.128.214
scripts/verify-on-device.sh root@192.168.128.214
```

The verifier checks:

- systemd service is active
- SteamOS Manager sees `TdpLimit1`
- `steamosctl set-tdp-limit` updates central D-Bus state
- the remote D-Bus service reports the same value
- Intel RAPL PL1 matches the requested value
- TDP is restored to the requested restore wattage
- no systemd failed units remain

## Editing rules for agents

- Add or update a failing unit test before changing backend behavior.
- Keep D-Bus names stable unless there is a migration note.
- Do not add a new hardware profile without a `collect-device-info.sh` capture
  summarized in `docs/hardware/`.
- Do not make boot-time TDP enforcement the default without documenting the
  SteamOS policy interaction.
