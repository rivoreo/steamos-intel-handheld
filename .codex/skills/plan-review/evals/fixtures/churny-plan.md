# Technical Design: Role Card Draft Auto-Save

## Problem

Creators lose in-progress role card edits when the browser tab closes. We want
periodic auto-save of drafts.

## Scope

- Server: new draft storage endpoints
- Desktop + Mobile: editor auto-save timer

## Design

Every 30 seconds the editor serializes the current form state and POSTs it to
`/rolecard/draft/save`. The server stores the latest draft per role per
account in a new `role_card_draft` table. On editor open, the client GETs
`/rolecard/draft/latest` and offers to restore.

## API

- `POST /rolecard/draft/save` — body: roleId, payload (JSON string)
- `GET /rolecard/draft/latest?roleId=...`

## Data Model

`role_card_draft(id, account_id, role_id, payload TEXT, updated_at)`

## Rollout

Ship server first, then both frontends.

## Acceptance Criteria

- Draft is restored after closing and reopening the editor
- No data loss for drafts younger than 30 seconds is NOT guaranteed
