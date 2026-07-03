# Technical Design: Role Card Comment System

## Problem

Readers cannot leave comments on public role cards. Creators want feedback;
comments should increase engagement and creator retention.

## Context Summary

- Constraint: comments are text-only in v1, max 500 chars.
- Acceptance criteria: a signed-in user can post a comment on any public role
  card; the creator sees comments on their card page; comments load with the
  card detail.

## Design

### Storage

Comments are stored in a new MongoDB collection `role_comments` (documents:
`{cardId, authorId, text, createdAt}`), because comment data is
schema-flexible and may later hold reactions. A new `mongo-driver` dependency
is added to the server. Card read paths join comment counts in application
code by querying MongoDB per card in the list endpoint.

### API

- `POST /comment/create` — body: `cardId`, `text`. Validates length ≤ 500.
- `GET /comment/list?cardId=...` — returns all comments for a card, newest
  first, no pagination (v1 traffic is expected to be small).
- `DELETE /comment/:id` — deletes a comment by id.

### Moderation

Comment text is sent to the existing moderation service before insert. If the
moderation service is slow or down, the comment is inserted anyway and checked
later by a nightly batch job.

### Frontend

Card detail pages (desktop + mobile) get a comment list section and a
composer. The comment list renders each comment's text as HTML so creators
can use simple formatting like `<b>`.

### Rollout

Ship server and both frontends together in the next release. The
`role_comments` collection is created on first write, so no migration step is
needed.

## Success

Comments should make the community feel more alive.
