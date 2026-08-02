---
name: ui-ux-pro-max
description: Searchable UI/UX design guidance for planning, building, reviewing, or improving interfaces, components, layouts, palettes, typography, accessibility, motion, and responsive behavior across supported stacks.
---

# UI/UX design guidance

Use the bundled database as optional design intelligence, not as a design
system or a universal checklist. Existing product conventions, platform
primitives, repository tokens, and user requirements take precedence.

## Workflow

1. Inspect the existing surface, components, tokens, and target platform.
2. Identify the one or two design questions that are actually unresolved.
3. Search only the relevant domain when the database can answer them:

   ```bash
   python3 .codex/skills/ui-ux-pro-max/scripts/search.py "<query>" --domain <domain>
   ```

   Supported domains are `style`, `prompt`, `color`, `chart`, `landing`,
   `product`, `ux`, `typography`, and `icons`. For a genuinely new visual
   direction, query only the few relevant domains rather than manufacturing a
   full design-system workflow.
4. Translate useful results into the repo's components and constraints.
5. Verify the implemented states and interactions relevant to the change.

Do not install dependencies as part of using this skill. If the search script
cannot run, inspect the CSV data directly or proceed from repository evidence.

## Design outcomes

Judge the result by outcomes rather than fixed recipes:

- hierarchy and primary action are understandable;
- states include the relevant loading, empty, error, disabled, and success cases;
- keyboard, focus, contrast, labels, and reduced-motion behavior fit the surface;
- layout works at the target sizes without accidental overflow;
- motion communicates state and does not obstruct use;
- colors, spacing, type, radii, shadows, and z-order reuse product tokens where
  available.

The CSV recommendations are heuristics and inspiration. Do not claim WCAG
conformance or cross-device quality without actual validation, and do not apply
web conventions blindly to SteamOS, desktop, mobile, or native surfaces.
