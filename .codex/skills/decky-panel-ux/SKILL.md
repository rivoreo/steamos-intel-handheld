---
name: decky-panel-ux
description: Product and behavior boundaries for this repo's Decky QAM plugins (Game Power, Charge Limit) - who the audience is, what may appear in the always-visible panel, gamepad-native controls, and localisation rules. Use when editing decky/*/src/index.tsx, plugin copy, panel layout, or the plugin backend surface.
---

# Decky panel UX

The audience is **a player mid-game holding a gamepad**, not a developer reading
a scheduler trace. They opened the QAM because something felt wrong, or they want
to change one thing and get back to playing.

That is the whole design constraint. Everything below follows from it.

## Shape of a panel

1. **One glanceable status** - a plain sentence answering "what is it doing right
   now", plus at most one line of numbers. Not a field dump.
2. **One primary control** - the thing they came to change.
3. **Secondary controls** only if they are genuinely part of normal use.
4. **Technical detail behind an opt-in toggle, default off.**

If a reader needs to know the project's vocabulary to parse a line, that line
does not belong in the always-visible panel.

## Say what is true, not what is claimed

- State facts about applied actuation ("steady, using less power") rather than
  internal confidence ("target-aware balancing"). Facts are checkable; claims
  drift from reality and cannot be verified by the user.
- **Never render a value the user did not choose as though they chose it.** A
  hardcoded default in a control seeds a real setting that one button press
  commits. Controls that mean "follow the system" must start empty and show what
  was detected.
- Bounds, steps and option lists come from the backend contract, never from
  literals in the frontend. The panel must not be able to offer something the
  daemon would reject.

## Collapse the daemon's axes

Internal state is allowed to be orthogonal; the panel is not. If two internal
knobs interact such that one silently does nothing depending on the other, expose
**one** user-facing choice and map it back. A user cannot be expected to hold an
interaction matrix in their head mid-game.

## Advice must reach someone who never opens the panel

A suggestion that only renders inside our own panel is invisible to the people
who most need it. Before adding advice-shaped UI, say where the user actually
encounters it. If the answer is "only if they happen to open Game Power", the
feature is not finished - either the setting needs a sensible default, or the
system needs to derive the value itself.

Ranked: a good default beats a derived value, a derived value beats advice, and
advice beats a raw knob. Most users do not know what to pick.

## Gamepad-native

- Use Decky's own focusable components (`SliderField`, `DropdownItem`,
  `ToggleField`, `ButtonItem`, `Field`). A raw `<input type="range">` cannot be
  focused or nudged with a D-pad.
- Minimise focusable elements; every extra one is another press to scroll past.
- The panel is ~310 px wide. Assume wrapping, never assume a table fits.

## Live, not a snapshot

The panel is usually the only place the running system is observable. Poll while
it is open rather than showing whatever was true when it was opened, and never
clobber a value the user is currently adjusting.

## Localisation

English and Traditional Chinese are both first-class, always in the same change.
Copy lives in one table keyed by locale; no inline strings in JSX. Locale comes
from the Steam client language with a navigator fallback.

## Boundaries

- **No scheduler vocabulary in always-visible UI.** The asset tests enforce a
  forbidden-word list (core naming, clock/limit internals, cgroup and scheduler
  knobs). Extend that list rather than working around it.
- Actions with device-visible side effects need an explicit press. Do not apply
  anything on mount, on poll, or on panel open.
- The plugin frontend holds no policy. It reads state and calls named backend
  methods; decisions live in the daemon.
- Ship `dist/index.js` rebuilt in the same change as `src/index.tsx` - the
  device loads the bundle, and the asset tests check both.
- Diagnostics may show anything, but must stay behind the default-off toggle and
  must never be the only place a normal user can find something they need.
- Scheduler semantics belong in [[game-power-scheduler]], not here.
