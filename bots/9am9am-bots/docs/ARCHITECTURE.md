# Architecture

9AM9AM Bots is a web app (`web/`) plus a Node server (`server/`) that runs every Bot's agent loop. The model is Grok 4.7 through the xAI Responses API. Each user's Bots act on one Orgo cloud computer and use apps connected through Composio.

## Components

| Piece | Where | What it does |
|---|---|---|
| API + realtime | `server/src/routes*.ts`, `lib/hub.ts` | JSON over HTTP, plus one Server-Sent Events stream per user (`GET /api/events`) carrying row updates, live state, roster changes and computer state |
| Agent loop | `server/src/agent/loop.ts` | Per-Bot serial queue. Each turn builds the system prompt and history, streams `grok-4.7` with function tools plus server-side `web_search`/`x_search`, runs tool calls, and repeats until the model answers |
| Tools | `server/src/agent/tools.ts` | Computer use (`computer_*`, `open_browser`, `shell`, `save_file`), memory (`remember`/`recall`), routines (cron and app events), tasks, `send_to_bot`, subagents, group chats, `ask_user`, `request_app_connection`, `stay_silent`, plus Composio's meta tools for 1,000+ apps |
| Memory | `server/src/agent/memory.ts`, `agent/prompt.ts` | Compacts long histories with xAI `POST /v1/responses/compact` (falls back to a summary). Consolidates durable facts after a chat goes idle. Keeps a bounded memory section in the prompt |
| Computers | `server/src/computer.ts`, `orgo.ts`, `idle.ts` | One Orgo Linux VM per user, with one Orgo *screen* (a separate X display with its own desktop) per Bot, up to 4, leased least-recently-used among idle Bots. Auto-suspends after 20 idle minutes and wakes on the next computer action |
| Viewer | `server/src/vnc.ts` | WebSocket relay between the browser (noVNC) and Orgo's websockify. The server completes VNC auth upstream, so the VM password, which is root-equivalent, never reaches the browser. It then offers the browser an unauthenticated RFB session. In *watch* mode, keyboard, pointer and clipboard messages are dropped on the server |
| Apps | `server/src/composio.ts`, `triggers.ts` | Catalog (`/api/v3.1/toolkits`), per-user sessions, connect links, tool execution, and triggers (realtime subscription) for event routines. Sign-in uses a Composio-managed Gmail connection and reads the profile email |
| Scheduling | `server/src/routines.ts`, `proactive.ts` | Cron routines (croner), event routines, and proactive check-ins for the primary Bot |
| Voice | `server/src/voice.ts`, `web/src/voice/*` | Mints short-lived xAI realtime client secrets. The browser connects to `wss://api.x.ai/v1/realtime` directly. A small voice agent relays real work to the Bot's text loop (`send_task`) and speaks the result. Also proxies TTS (preview) and STT (dictation) |
| UI | `web/src` | React + zustand. Sidebar, chat, details panel (activity, tasks, library), modals, settings, viewer, voice call bar |

## Transcript model

A conversation is an append-mostly list of **rows**: `seq` (position), `updatedSeq` (a per-Bot revision counter), `kind` and a JSON `body`.

- **Streaming:** a streaming reply is the same row rewritten with a higher `updatedSeq`, so clients just upsert by `seq`.
- **Resetting:** a per-Bot `generation` counter resets the conversation ("new conversation").
- **Model history:** the Responses API input items live separately in `llm_items` (`store:false`), and encrypted reasoning is passed back unchanged.
  - Only the last 3 screenshots stay inline.
  - Orphaned tool calls from an interrupted turn are repaired before the next call.

Row kinds:
- `message`: user or assistant, optionally *from* another Bot or a voice call
- `tool-call`: shown in the UI only as an activity line and a collapsible step list
- `card`: approval, question, connect, draft
- `event`: routine created or run, voice call, group changes
- `notice`

## Approvals

The loop checks policy before executing each tool call:
- A Bot's own computer and shell run freely.
- Composio tools whose slug reads as read-only (`*_GET`, `*_LIST`, `*_SEARCH`, …) run automatically.
- Anything else posts an **approval card** and pauses the turn, saving the pending calls in `pending_turns`. The turn resumes when the user answers.
  - **Allow** runs the call once.
  - **Always allow** stores a per-tool policy.
  - **Don't allow** sends the model a refusal telling it not to retry.
- A new user message closes any pending approval as expired.

## Bots working together

- **`send_to_bot`:** delivers a message to another Bot's queue. The reply comes back the same way.
- **Subagents:** hidden child Bots with their own context and screen. They report back with `send_to_bot` and are removed with `stop_subagent`.
- **Group chats:** a Bot row with `kind='room'` and a member list. A user message goes to the @mentioned members, or to all members (up to 3). Each member answers in the group with `post_to_group`.

## Data

SQLite (`data/9am9am.db`, WAL) holds:
- users and sessions
- Bots, rows, model history
- approvals, policies
- routines, tasks, memories, skills
- computers and screen leases
- Composio session ids, compactions, check-in settings

Secrets live only in `.env` on the server.

## External services

| Service | Used for | Notes |
|---|---|---|
| xAI | `grok-4.7` (Responses API), realtime voice, TTS, STT | `prompt_cache_key` per Bot and generation, `safety_identifier` from a hashed user id |
| Orgo | VMs, screens, actions (screenshot, click, type, key, scroll, drag, bash), VNC, files | Rate limit is about 600 control calls/min per computer. An in-VM executor is the planned next step |
| Composio | Sign-in, connectors, marketplace, triggers | Uses Composio-managed OAuth apps by default. Register your own OAuth clients for production branding |
