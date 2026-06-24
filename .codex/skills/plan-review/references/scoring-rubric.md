# Scoring Rubric & Output Format

## Score Definitions

| Score | Label | Meaning | Blocking? |
|-------|-------|---------|-----------|
| **+2** | Strong Approve | No issues found. Plan is solid and ready. | No |
| **+1** | Approve with Comments | Minor suggestions that don't block. Author should consider but not required to act. | No |
| **-1** | Request Changes | Concerns that MUST be addressed before approval. Specific issues identified. | **Yes** |
| **-2** | Fundamental Issues | Core design flaw requiring major rework. | **Yes (hard)** |

## Score Calibration Examples

### +2 Examples
- Plan covers all platforms, has clear API contracts, well-defined acceptance criteria, no gaps found
- Minor stylistic preferences exist but are truly optional and would not improve the plan materially

### +1 Examples
- A non-critical code path is missing error handling consideration
- An alternative approach exists that might be slightly better but current approach is acceptable
- Documentation of a decision rationale could be clearer
- A minor edge case (e.g., empty list display) is mentioned but not fully specified

### -1 Examples
- No error handling strategy defined for a user-facing feature
- API contract between server and client is ambiguous or inconsistent
- A common user scenario is not addressed in the plan
- Missing i18n consideration for a user-visible feature
- Performance concern for a list page with no pagination/virtualization strategy

### -2 Examples
- Architectural approach is fundamentally incompatible with existing codebase
- Security vulnerability is built into the design (e.g., no auth on sensitive endpoint)
- Plan requires a technology/dependency that is unavailable or incompatible
- Core data model design would cause data loss or corruption

## Severity Mapping

| Severity | Score Impact | Description |
|----------|-------------|-------------|
| **critical** | Forces -2 | Fundamental flaw, cannot proceed |
| **major** | Forces -1 or lower | Significant issue requiring fix before approval |
| **minor** | Compatible with +1 | Small issue, suggestion level |
| **suggestion** | Compatible with +2 | Optional improvement, no impact on approval |

## Required JSON Output Format

Each reviewer MUST output their review as a JSON block within markdown code fences:

```json
{
  "reviewer": "A|B|C|D|E",
  "iteration": 1,
  "score": 2,
  "summary": "One-paragraph overall assessment of the plan",
  "findings": [
    {
      "severity": "critical|major|minor|suggestion",
      "category": "architecture|completeness|security|conventions|ux|product|...",
      "finding": "Clear description of the issue or observation",
      "recommendation": "Specific suggested fix or improvement",
      "planSection": "Which part of the bounded review surface this relates to",
      "evidence": "Specific section, contradiction, requirement, code reference, or acceptance criterion",
      "attributionEvidence": "When iteration > 1, cite originalSurfaceSnapshot/currentSurfaceSnapshot/latestRevisionDiff evidence used for novelIssueSource",
      "novelIssueSource": "none|revision_introduced|latent_missed|scope_expansion|unsupported"
    }
  ],
  "strengths": [
    "List of things the plan does well"
  ],
  "verdict": "APPROVE|REVISE|REJECT"
}
```

### Field Requirements

| Field | Required | Notes |
|-------|----------|-------|
| `reviewer` | Yes | Must match assigned role (A/B/C/D/E) |
| `iteration` | Yes | Current review loop iteration number. This is not capped by this skill; the outer harness owns runtime budget. |
| `score` | Yes | Must be one of: 2, 1, -1, -2 |
| `summary` | Yes | 1-3 sentences |
| `findings` | Yes | Array, can be empty for +2. Critical/major findings require `evidence`, `novelIssueSource`, and attribution evidence when iteration > 1. |
| `strengths` | Yes | Array, at least 1 item |
| `verdict` | Yes | APPROVE (+2/+1), REVISE (-1), REJECT (-2) |

### Novel Issue Source

Use `novelIssueSource` to classify critical/major findings that appear after
the discovery sweep. This is how the loop distinguishes a real regression from
a review-process miss:

| Source | Use when |
|--------|----------|
| `none` | First-pass findings, already-ledgered findings, minor comments, or suggestions |
| `revision_introduced` | The latest revision created the blocker. Keep it in the normal open ledger loop. |
| `latent_missed` | The blocker existed inside the original bounded surface and was missed by prior full sweeps. Consecutive cases may become `review_process_defect`. |
| `scope_expansion` | The finding requires reviewing unrelated documents/files/tests/harness behavior or facts outside the frozen surface. |
| `unsupported` | The finding lacks concrete evidence or is only a preference. It must not block. |

### Score-Verdict Consistency

| Score | Valid Verdict |
|-------|--------------|
| +2 | APPROVE only |
| +1 | APPROVE only |
| -1 | REVISE only |
| -2 | REJECT only |

## JSON Parsing Fallback Strategy (3 Layers)

When parsing reviewer output, apply in order:

### Layer 1: Direct JSON Parse
1. Search for JSON within markdown code fences (```json ... ```)
2. If not found, search for bare JSON objects ({ ... })
3. Parse and validate required fields

### Layer 2: Regex Field Extraction
If Layer 1 fails, extract key fields via regex:
- `"score"\s*:\s*(-?[12])` → score
- `"verdict"\s*:\s*"(APPROVE|REVISE|REJECT)"` → verdict
- `"summary"\s*:\s*"([^"]+)"` → summary
- Count `"severity"` occurrences for findings count

### Layer 3: Natural Language Fallback
If Layer 2 fails, search reviewer output for:
- Score keywords: `+2`, `+1`, `-1`, `-2`, `strong approve`, `approve`, `request changes`, `reject`
- Verdict keywords: `APPROVE`, `REVISE`, `REJECT`
- Any bullet points starting with issue/concern/finding for findings extraction

If all 3 layers fail, treat as reviewer failure (trigger retry).

## Revision Changelog Format

When the author revises the plan after a review iteration, produce a changelog in this format:

```markdown
## Revision Changelog (Iteration N -> N+1)

1. **[CRITICAL/Reviewer B]** Added error handling strategy for API timeouts
   - Finding: "No error handling defined for network failures"
   - Change: Added "Error Handling" section with retry and fallback strategies

2. **[MAJOR/Reviewer A]** Clarified API contract between server and client
   - Finding: "API response format ambiguous"
   - Change: Added explicit request/response schemas in API section

3. **[MINOR/Reviewer D]** Added loading state design for list pages
   - Finding: "No loading indicator specified"
   - Change: Added skeleton loading pattern to UX section
```

Each entry: severity tag + source reviewer → finding summary → change description.
