# Feature Plan: Mobile Bookmarks Page

## Problem

Users cannot revisit favourite messages. Add a bookmarks page on mobile that
lists messages the user starred inside any conversation.

## Scope

- Mobile (uni-app Vue 2) only for now
- Server endpoint `GET /bookmark/list` already exists

## UI Design

New page `pages/bookmarks/bookmarks.vue`, reachable from the Mine tab.

- Page header: fixed bar, height 44px, background `#0d0d0d`, title
  "我的收藏" centered, font-size 17px
- Bookmark card: background `#1a1b20`, border-radius 12px, padding 14px,
  margin 10px 16px; message excerpt max 3 lines, role avatar 36px circle at
  left, timestamp in `#8a8f98` 12px at bottom right
- Tapping a card deep-links into the conversation at that message
- Pull-to-refresh at top; infinite scroll pagination (20 per page)
- While the list loads, show `uni.showLoading({ title: '載入中' })` and hide
  it when the request returns
- Long-press a card to remove the bookmark (calls `DELETE /bookmark/:id`,
  then removes the row with a 0.4s fade-out animation)

## Data Flow

On page show, fetch page 1. Store items in local `data.list`. Append pages on
`onReachBottom`.

## Acceptance Criteria

- Starred messages appear in the list, newest first
- Tapping navigates to the right conversation position
- Removing a bookmark updates the list without a full reload

## Rollout

Ship in next mobile release.
