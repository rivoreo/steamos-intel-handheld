# Game Power Decky Plugin

This Decky Loader plugin is a safe control surface for the
steamos-intel-handheld game-power governor.

The plugin exposes status, short diagnostics, mode selection, and packaged
default restore. It intentionally does not expose measured low-level scheduler
values as user controls.

The V10 framework adds intent-framed controls: a power-intent (persona)
selector, a consent-gated frame-limit helper, a live package-power vs
soft-budget row with a boost indicator, and a frame-feed status chip. These are
additive and degrade gracefully: they blank out for the shipped `gpu-priority`
default, stale snapshots, or `off`/`observe`. The frame-limit helper runs in the
gamescope session (device-unverified) and never fabricates a read-back.
