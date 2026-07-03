# Technical Design: Conversation Export (Markdown)

## Problem

Users want to export a conversation as a Markdown file for archiving.

## Context Summary

- Constraint: export is client-triggered, server-rendered, max 1 export
  request per user per minute (rate limited via existing middleware).
- ASSUMPTION: export volume is low (<200/day projected from current DAU); a
  synchronous endpoint is acceptable and no job queue is needed for v1.
- Acceptance criteria: a signed-in user can download a .md file of any
  conversation they own; non-owners get 403; deleted conversations get 404.

## Design

New endpoint `GET /conversation/:id/export` behind existing auth middleware.
Handler loads the conversation via the existing `conversationService.Get`
(which already enforces ownership), renders messages through a new pure
function `RenderMarkdown(msgs)`, and streams the result with
`Content-Disposition: attachment`.

## Performance

Conversations are capped at 2,000 messages by the existing retention policy.
Rendering 2,000 messages was benchmarked at ~8ms; the response is streamed, so
no buffering concern. The endpoint reuses the existing per-user rate limiter
(1/min) to bound load.

## Error Handling

- 401 unauthenticated (middleware), 403 non-owner, 404 missing/deleted
- Render errors return 500 with the standard error envelope; no partial file

## Observability

Counter `lunatalk_conversation_export_total{status}` on the shared registry;
verification: `increase(lunatalk_conversation_export_total[5m])` after deploy.

## Testing (TDD)

1. Failing router test: owner gets 200 + attachment header (Red)
2. Failing test: non-owner 403, deleted 404
3. Unit tests for `RenderMarkdown`: empty conversation, system messages,
   multibyte content

## Rollout

Server-only change; deploy via standard charter pipeline. No frontend change
in v1 (users hit the URL from the existing share menu link).
