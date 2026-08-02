# 2026-07-05 Codex Sub-Agent Slot Handoff

## Copy Prompt For New Session

Use this prompt to continue in a fresh Codex session:

```text
請接手 /Users/bmy001/Work/steamos-intel-handheld 這個 repo。先讀 AGENTS.md。

上一個 session 的主要問題不是 repo 代碼，而是 Codex multi-agent slot 卡住：
- 不要再直接調 multi_agent_v1.close_agent；它曾經在這個 session 上卡住。
- 已完成的 6 個 sub-agent 原本佔住 slot，其中 5 個在 SQLite 裡仍標記 open。
- 已經備份 /Users/bmy001/.codex/state_5.sqlite 到 /tmp/codex-state_5-before-agent-edge-close.sqlite。
- 已經把 /Users/bmy001/.codex/state_5.sqlite 的 thread_spawn_edges 中這 6 個 child_thread_id 全修成 closed：
  - 019f2d63-a847-7b81-9fea-dc646a39841b
  - 019f2d63-c2fe-72b2-aae7-89c7bc1c5dab
  - 019f2d81-4d21-7220-854f-563da7aae1be
  - 019f2da0-bbf1-70e1-9a2f-814edf6a563e
  - 019f2e74-cc99-7103-9b14-576040a9e727
  - 019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec
- 修完後 SQLite open count 是 0，但上一個 live session 的 app-server 內存態仍然回 agent thread limit reached。
- 所以新 session 第一件事是測一次 spawn_agent 是否恢復；如果恢復，立刻關閉/清理 probe agent，不要留下新 open slot。

如果 spawn_agent 仍然失敗：
1. 先只讀確認 /Users/bmy001/.codex/state_5.sqlite 的 thread_spawn_edges open count。
2. 確認 app-server 狀態：codex app-server daemon version。
3. 注意目前 app-server 0.142.5 可能不是 codex app-server daemon 管理，上一個 session 跑 codex app-server daemon restart 回報：app server is running but is not managed by codex app-server daemon。
4. 真正會刷新內存態的下一步可能是重啟 Codex Desktop / app-server，但這會中斷當前會話；不要靜默 kill，先明確告知風險。

恢復 sub-agent 後，回到原本工程任務：Game Power governor / Decky 插件 / V6 後續優化。涉及代碼改動按 AGENTS.md 選擇驗證。
```

## Current State

- Repo: `/Users/bmy001/Work/steamos-intel-handheld`
- Current date: 2026-07-05
- Current Codex CLI/app-server version from `codex app-server daemon version`: `0.142.5`
- App-server control socket: `/Users/bmy001/.codex/app-server-control/app-server-control.sock`
- Important local DB: `/Users/bmy001/.codex/state_5.sqlite`
- SQLite backup before manual edge repair: `/tmp/codex-state_5-before-agent-edge-close.sqlite`
- Generated app-server protocol schemas: `/tmp/codex-appserver-schema/`

The persistent DB has been repaired:

```sql
SELECT status, COUNT(*) AS count
FROM thread_spawn_edges
WHERE parent_thread_id='019f28b0-e782-76a0-9c4a-33b7878899be'
GROUP BY status;
```

Current result:

```text
status  count
------  -----
closed  129
```

The six problem child threads are all `closed` in SQLite now:

```text
019f2d63-a847-7b81-9fea-dc646a39841b  closed
019f2d63-c2fe-72b2-aae7-89c7bc1c5dab  closed
019f2d81-4d21-7220-854f-563da7aae1be  closed
019f2da0-bbf1-70e1-9a2f-814edf6a563e  closed
019f2e74-cc99-7103-9b14-576040a9e727  closed
019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec  closed
```

## What Happened

The user asked to close stuck sub-agents without using `close_agent`.

Prior attempts with `multi_agent_v1.close_agent` had hung:

- Batch close on all six old agents was aborted after a long hang.
- Single close on `019f2d63-a847-7b81-9fea-dc646a39841b` also hung.

Do not repeat that path unless the user explicitly asks.

All six agents were confirmed completed via `multi_agent_v1.wait_agent`, but the live session still refused new agents with:

```text
collab spawn failed: agent thread limit reached
```

## Agents Involved

Old sub-agent IDs:

- `019f2d63-a847-7b81-9fea-dc646a39841b` - Hubble the 2nd
- `019f2d63-c2fe-72b2-aae7-89c7bc1c5dab` - Chandrasekhar the 2nd
- `019f2d81-4d21-7220-854f-563da7aae1be` - Ohm the 2nd
- `019f2da0-bbf1-70e1-9a2f-814edf6a563e` - Arendt the 2nd
- `019f2e74-cc99-7103-9b14-576040a9e727` - Turing the 2nd
- `019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec` - Planck the 2nd

Parent thread:

- `019f28b0-e782-76a0-9c4a-33b7878899be`

## What Was Tried

### Safe CLI/Protocol Discovery

Read help and protocol surfaces:

```bash
codex --help
codex app-server --help
codex remote-control --help
codex archive --help
codex delete --help
codex debug --help
codex debug app-server --help
codex app-server daemon --help
codex app-server proxy --help
codex app-server generate-json-schema --out /tmp/codex-appserver-schema --experimental
```

Relevant finding:

- `archive/delete` are saved-session management, not live agent slot cleanup.
- Protocol schema includes `thread/loaded/list`, `thread/archive`, `thread/delete`, `thread/closed`, and collab tool enum `closeAgent`.
- There is no obvious supported per-agent cleanup method besides close-agent.

### Direct Protocol Probes

Tried to query app-server through the control socket:

- `codex app-server proxy --sock ...` with newline JSON-RPC
- `codex app-server proxy --sock ...` with `Content-Length` framed JSON-RPC
- Direct Python Unix socket with `Content-Length` framed JSON-RPC
- `curl --unix-socket ... /api/version`, `/healthz`, `/`

These did not produce useful management responses from the live socket. The normal CLI command below did work:

```bash
codex app-server daemon version
```

It returned:

```json
{"status":"running","managedCodexPath":"/Users/bmy001/.codex/packages/standalone/current/codex","managedCodexVersion":null,"socketPath":"/Users/bmy001/.codex/app-server-control/app-server-control.sock","cliVersion":"0.142.5","appServerVersion":"0.142.5"}
```

### Archive Test

Tried:

```bash
codex archive 019f2d63-a847-7b81-9fea-dc646a39841b
```

It succeeded, but `spawn_agent` still failed with `agent thread limit reached`.

Then reverted:

```bash
codex unarchive 019f2d63-a847-7b81-9fea-dc646a39841b
```

Conclusion: `codex archive` does not release multi-agent slots.

### SQLite Investigation And Repair

Tables in `/Users/bmy001/.codex/state_5.sqlite` include:

- `threads`
- `thread_spawn_edges`
- `agent_jobs`
- `agent_job_items`

Schema showed:

```sql
CREATE TABLE thread_spawn_edges (
    parent_thread_id TEXT NOT NULL,
    child_thread_id TEXT NOT NULL PRIMARY KEY,
    status TEXT NOT NULL
);
```

Before repair:

- `019f2d63-a847-...` was already `closed`.
- The other five were still `open`.
- Parent open count was 5, exactly matching the apparent slot limit.

Backup command used:

```bash
sqlite3 /Users/bmy001/.codex/state_5.sqlite ".backup '/tmp/codex-state_5-before-agent-edge-close.sqlite'"
```

Repair command used:

```bash
sqlite3 /Users/bmy001/.codex/state_5.sqlite "UPDATE thread_spawn_edges SET status='closed' WHERE status='open' AND child_thread_id IN ('019f2d63-c2fe-72b2-aae7-89c7bc1c5dab','019f2d81-4d21-7220-854f-563da7aae1be','019f2da0-bbf1-70e1-9a2f-814edf6a563e','019f2e74-cc99-7103-9b14-576040a9e727','019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec');"
```

After repair:

- SQLite `thread_spawn_edges` open count is 0.
- Live `spawn_agent` in the same session still fails with `agent thread limit reached`.

Conclusion: current app-server/live session keeps an in-memory slot counter or loaded thread state that did not reload from SQLite.

### Restart Attempt

Tried:

```bash
codex app-server daemon restart
```

It failed with:

```text
Error: app server is running but is not managed by codex app-server daemon
```

Process investigation showed at least:

- Desktop app-server: `/Applications/Codex.app/Contents/Resources/codex app-server --analytics-default-enabled`
- SSH/control socket server: `.../codex app-server --listen unix://`

The socket owner for `/Users/bmy001/.codex/app-server-control/app-server-control.sock` was the standalone app-server process. Do not kill it silently; that may interrupt the current conversation or remote-control state.

## Important Files And Artifacts

- Handoff doc: `docs/handoffs/2026-07-05-codex-subagent-slot-handoff.md`
- SQLite state DB: `/Users/bmy001/.codex/state_5.sqlite`
- SQLite backup before repair: `/tmp/codex-state_5-before-agent-edge-close.sqlite`
- App-server protocol schema bundle: `/tmp/codex-appserver-schema/`
- App-server log: `/Users/bmy001/.codex/app-server-control/app-server.log`
- Main parent rollout: `/Users/bmy001/.codex/sessions/2026/07/03/rollout-2026-07-03T23-55-10-019f28b0-e782-76a0-9c4a-33b7878899be.jsonl`
- Problem child rollout paths:
  - `/Users/bmy001/.codex/sessions/2026/07/04/rollout-2026-07-04T21-48-53-019f2d63-a847-7b81-9fea-dc646a39841b.jsonl`
  - `/Users/bmy001/.codex/sessions/2026/07/04/rollout-2026-07-04T21-49-00-019f2d63-c2fe-72b2-aae7-89c7bc1c5dab.jsonl`
  - `/Users/bmy001/.codex/sessions/2026/07/04/rollout-2026-07-04T22-21-16-019f2d81-4d21-7220-854f-563da7aae1be.jsonl`
  - `/Users/bmy001/.codex/sessions/2026/07/04/rollout-2026-07-04T22-55-36-019f2da0-bbf1-70e1-9a2f-814edf6a563e.jsonl`
  - `/Users/bmy001/.codex/sessions/2026/07/05/rollout-2026-07-05T02-47-14-019f2e74-cc99-7103-9b14-576040a9e727.jsonl`
  - `/Users/bmy001/.codex/sessions/2026/07/05/rollout-2026-07-05T02-47-20-019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec.jsonl`

## Recommended Next Steps

1. Start a fresh session.
2. Read this document and `AGENTS.md`.
3. Test `spawn_agent` once with a minimal probe.
4. If the probe succeeds, immediately close/clean that probe agent and proceed with the original project work.
5. If the probe still fails, verify DB open count and app-server state; then consider a deliberate app-server/Desktop restart only after warning the user it may interrupt the active session.
6. Resume the real project work only after sub-agent capacity is known, because the user explicitly wants sub-agent review/research for V6/V7-style scheduler work.

## Verification Run For This Handoff

This was documentation-only, not code or policy. No repository checks were run.

Commands run to verify the handoff facts:

```bash
find docs -maxdepth 3 -type d | sort
git status --short
sqlite3 -header -column /Users/bmy001/.codex/state_5.sqlite "SELECT status, COUNT(*) AS count FROM thread_spawn_edges WHERE parent_thread_id='019f28b0-e782-76a0-9c4a-33b7878899be' GROUP BY status;"
sqlite3 -header -column /Users/bmy001/.codex/state_5.sqlite "SELECT child_thread_id, status FROM thread_spawn_edges WHERE child_thread_id IN ('019f2d63-a847-7b81-9fea-dc646a39841b','019f2d63-c2fe-72b2-aae7-89c7bc1c5dab','019f2d81-4d21-7220-854f-563da7aae1be','019f2da0-bbf1-70e1-9a2f-814edf6a563e','019f2e74-cc99-7103-9b14-576040a9e727','019f2e74-e2b5-7ae1-8731-ee2a4fdb1dec') ORDER BY child_thread_id;"
ls -l /tmp/codex-state_5-before-agent-edge-close.sqlite /tmp/codex-appserver-schema/codex_app_server_protocol.v2.schemas.json
codex app-server daemon version
```
