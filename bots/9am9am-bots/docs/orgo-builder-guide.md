# Orgo builder's guide for 9AM9AM Bots

How to build 9AM9AM Bots (one persistent Orgo VM per user, one screen per Bot) on Orgo. Everything here comes from Orgo's official docs.

- **Sources:** the full docs dump `docs.orgo.ai/llms-full.txt` (24,038 lines, every page incl. the full API reference; read 2026-11-09) and the OpenAPI spec `https://docs.orgo.ai/api-reference/openapi.json` (Orgo API 2.0.0, 101 operations).
- **Citations:** `(§Section title, L1234)` points at a section heading and line number in the dump.
- **Markers:** **[inferred]** means my conclusion and not a documented fact. **[perm-table]** means the endpoint appears only in the generated API-key permission tables (§API key permissions, L9031), not in the OpenAPI spec or a reference page, so its request and response shapes are undocumented.
- **Not done:** no Orgo endpoint was called while writing this.

---

## 0. TL;DR: what matters for 9AM9AM Bots

1. **Screens fit "one screen per Bot", but cap at 4 per VM.** A Linux computer boots with one screen (`default`, X display `:99`) and can run **up to four** (§List screens, L20291; §Create a screen, L20417). Each screen is its own X server with its own pointer and window manager. Target one with `?screen=<id>` on screenshot, click, mouse-move, drag, type, key, scroll and bash. That is **at most 4 bots with their own live screen per user at once.** Bots beyond that need a screen pool (see §10).
2. **Orgo's hosted agent (`/v1/chat/completions`) does not fit.** It takes no `screen` parameter (the spec body is only `computer_id, messages, model, stream, max_steps, thread_id`). It runs only OpenAI- and Anthropic-backed models, with no xAI or BYOK header for Grok (§Bring your own model key, L3721). **Run our own Grok loop.** Orgo is just the computer.
3. **There is no inbound networking to the VM.** "A computer has no public inbound hostname" (§OpenClaw, L3794). The only HTTP way in is Orgo's authenticated proxy to Orgo's own Desktop API. An agent host inside the VM must **dial out** to our server (WebSocket/SSE). There is no systemd either. Run services under the template's supervisord `apps.services`.
4. **Sleep and wake:** `POST /computers/{id}/suspend` keeps memory and processes. Resume on the host takes "seconds", and a wake from storage takes "about a minute" (§Resume computer, L17049). **Hourly computers can't suspend** (`409 HOURLY_USE_STOP`, L17030), and a stop/start cold-boots and loses memory. Plan computers hold their pool RAM and vCPU even while asleep (§Account limits, L2480), so sleeping a plan computer saves no money.
5. **Fleet ceiling: 999 computers per account.** That is 50 on a plan, then hourly from credit up to 999. Past that needs a contract, up to 9999 (§Computers per account, L2529). Per-computer control-call limit: **600/min** (§Plan rate limits, L2852). Account control calls: 6,000/min on Scale.
6. **Viewer:** noVNC against `wss://www.orgo.ai/desktops/{id}/ws/websockify?screen=<screenId>&token=<vnc_password>`. The password is **root on the VM**, and there is **no view-only credential** (§Embed computers, L4707, L4718). For Watching vs Take control, proxy the socket through our server and filter input there **[inferred design]**.
7. **Custom "9AM9AM Bots box" templates need the Scale plan** to publish, build or test-run (§Templates, L5814). Launching one works on any paid plan.

---

## 1. Auth, base URLs, keys, scoping, limits

### Base URLs and headers

| Surface | URL |
|---|---|
| REST | `https://www.orgo.ai/api` (L6, §API Reference L8643) |
| OpenAI-compatible | `https://www.orgo.ai/api/v1` (`/v1/chat/completions`, `/v1/threads`, `/v1/accounts`, `/v1/agents`, `/v1/support/ask`) |
| WebSockets | `wss://www.orgo.ai/desktops/{id}/ws/{websockify,terminal,audio,events,rtc,stats}` |
| Desktop API proxy | `https://www.orgo.ai/api/desktops/{id}/proxy/{path}` (e.g. `/proxy/health`) |
| Hosted MCP | `https://www.orgo.ai/mcp` |

- **Header:** `Authorization: Bearer <token>` on every request.
- **Errors:** JSON `{ "error": string, "code"?: string }`. `/v1/*` uses the nested OpenAI envelope `{ "error": { "type", "message", "code" } }` (§API Reference, L8789-8797). Branch on `code`, never on the message.
- **request_id:** desktop-side failures carry `request_id`, plus `upstream_status` when the in-VM agent answered non-2xx (§Troubleshooting, L4917-4935).
- **Idempotency:** create computer, extend (required there), capacity purchases, workflow create/run and agent signup accept an `Idempotency-Key` header. A retry replays the first answer with `Idempotent-Replayed: true`. Keys last a day (§Extend computer, L14975-14983).

### Token types (§Authentication, L8854)

| Prefix | What | Reach |
|---|---|---|
| `sk_live_…` | API key | Account-wide, workspace-scoped, or one-computer, plus optional per-group permissions and expiry |
| `orgo_mcp_…` | Hosted MCP OAuth sign-in | Computer, file and workspace-read endpoints only. Can't read VNC passwords or apply secrets (§MCP, L1494) |
| `orgo_vm_…` | Identity token minted inside a Linux VM | That one computer, about 1 hour (§Get an identity token, L4307) |

Any other bearer value falls through to browser-session auth and fails with `401` server-to-server.

### Key scope (§Key scopes and computer keys, L10380-10386)

| Scope | Create with `POST /api/keys` | Notes |
|---|---|---|
| Account | no `workspace_id`/`computer_id` | Needed for: creating workspaces, capacity, threads, `/secrets/apply`, `POST /api/secrets`, templates, clients |
| Workspace | `"workspace_id"` | Only that workspace's computers, files and screenshots, plus chat completions there. No templates, no threads, never manages people (§Authentication, L8883-8893) |
| One computer | `"computer_id"` (Linux only) | Start, stop, restart, suspend and resume plus control endpoints. Can't delete, resize, clone, fork or move (L10491). **Revoking one rotates the VM's VNC password** (L10521-10525) |

- **Permissions** (§API key permissions, L9040-9087): `full` (default), `read_only`, or custom `{group: none|read|write}` over 12 groups: `computers, control, files, templates, workspaces, members, secrets, ai, bops, billing, keys, workflows`.
- **Gotchas:**
  - Control **read** already takes screenshots and reads files (L9054).
  - A restricted key without Secrets write launches templates **without** injecting secrets (L6551).
  - Hourly launches need full access or Billing write (`403 HOURLY_NEEDS_BILLING`, L3183).
  - No-escalation rule: a key never mints a broader key (L9816).
- **Expiry:** 1-365 days or never. Reminder emails at 7d and 1d. An expired key gets `401 key_expired` and closes WebSockets with `4001` (§Key expiry and rotation, L10206-10249). Orgo never rotates keys you make.
- **Key CRUD:** `POST/GET /api/keys`, `GET/PATCH/DELETE /api/keys/{id}`. **Always send a JSON body, even `{}`.** A bodyless `POST /api/keys` *deletes your unnamed account key first* (L8945, L9867). Limit: 30 key creates or changes per hour per account (L2906).

### Computer keys (Orgo-managed `ORGO_API_KEY` on a VM) (§Key scopes and computer keys, L10392-10477)

- **How a VM gets one:** a template whose `secrets` declare `orgo_api_key` gets a computer key at create. This happens only if the workspace has a single user and the create used a full-access, non-expiring, account-wide key. The key is written to `/root/.env`, and rotations go to `/root/.orgo-secrets.env`. It rotates on use after 30 days, and the old secret lives 10 more minutes (L10276-10283).
- **Danger:** a computer key is **not held to its own computer**. It can call control endpoints on your other computers too (L10458-10459). Treat it like an account key. **Prefer identity tokens** (§8).
- **Revoked automatically** whenever the workspace stops being single-user: any invite (even Can chat), share link, workspace key, Invoicing client, MCP sign-in or one-computer key (L10467-10477).
- **Side effect:** if a computer holds a computer key, restricted or expiring keys get `403 computer_key_held` on its Control and AI endpoints (L10461-10463).

### Workspaces (§Workspaces, members and clients, L2220; §Create workspace, L13553)

- **Model:** a workspace groups computers, members, Secrets and settings. Up to **9,999 workspaces** per account on every plan (L2275). The name is 1-80 chars and unique, case-insensitive (L2708).
- **Members:** `Can chat` (chat and watch screens) or `Full access`. Seats are 0/1/5/25 per workspace on Free/Starter/Growth/Scale (L2263-2269).
- **Billing:** limits belong to the **workspace owner's account**. Computers in every owned workspace count together (L2477-2480).
- **Gotcha:** `GET /workspaces` is not read-only. With an account key on an account that has no workspaces, it *creates* one (L13702). There's no "list computers" endpoint. Read `desktops` from list/get workspace (L14502).

### Rate limits (§Plan rate limits, L2842-2890)

Limits are per account, never per key. Windows are a fixed minute, a clock hour, and a UTC day.

| What | Free | Starter | Growth | Scale |
|---|---|---|---|---|
| New computers / min · hour · day | 2·10·20 | 10·100·500 | 30·300·2,000 | 60·1,000·5,000 |
| Starts/stops/changes per min (account) | 10 | 60 | 120 | 300 |
| Starts/stops/changes per computer per min | 10 | 10 | 10 | 10 |
| Control calls per min (account) | 300 | 1,500 | 3,000 | 6,000 |
| **Control calls per computer per min** | 600 | 600 | 600 | 600 |
| Agent runs per min · at once | 5·1 | 30·5 | 60·20 | 120·50 |
| New workspaces per hour | 10 | 30 | 60 | 120 |
| Other API calls per min | 120 | 600 | 1,200 | 3,000 |

- **Control calls:** screenshot, click, drag, mouse-move, scroll, type, key, bash, exec, wait, Desktop-API proxy calls and terminal input. They count only for API keys, computer keys, identity tokens and MCP. Signed-in dashboard use isn't counted (L2862-2870).
- **Over a limit:** `429 rate_limited` with `limit` and `retry_after`, plus `Retry-After` and `RateLimit-*` headers (L2874-2886).
- **Endpoint-specific limits** (L2902-2964):
  - Identity token: 20 req/s per VM (L4438).
  - VNC WebSocket message: ≤16 MB.
  - Template publish 1/s, build 1/s with 10 queued, test-run 5 at once, 100 templates stored.
  - In-VM model gateway: 8 concurrent per computer.

### Account limits that matter for one VM per user (§Account limits and rate limits, L2468-3030)

- **Computers:**
  - 50 per account on a paid plan, counting plan and hourly computers in any state (running, stopped, asleep).
  - Past 50, each new computer runs **by the hour from credit**, up to **999**. Past that is contract only, up to 9999 (L2529-2557).
  - Copies (clone, fork, restore-as-new), GPU, macOS, images, snapshots and non-starter templates **can't** be created past the baseline (L2552). **[inferred]** This likely blocks launching a custom 9AM9AM Bots template past computer #50 unless Orgo grants the account its own baseline.
- **Pools:** vCPU and RAM 2/8 GB (Starter), 8/32 GB (Growth), 36/128 GB (Scale). A computer holds its pool share until deleted, even when stopped or asleep (L2480).
  - Self-serve add-ons go up to 800 GB RAM, 200 vCPU and 4 TB disk (L2521-2525).
  - Per-computer cap on your own is XL (6 vCPU/32 GB). The hard cap is 8 vCPU/64 GB/300 GB (L2751-2752).
- **Disk:** default 40 GB on current paid plans. It **only grows** (L2757-2760).
- **Request sizes:** most bodies ≤10 MB. Chat completions ≤150 MB. File upload ≤10 MB. Large upload and export ≤2 GB (L2990-2998).
- **One operation at a time:** concurrent start/stop answers `409 OPERATION_IN_PROGRESS`, and an identical retry joins the running one. Other cases (L2968-2986):
  - waking from storage: actions wait up to 150 s, then `409 computer_waking`
  - resuming: `503 resuming`
  - mid-suspend: `409 suspend_in_progress`
  - just woke: `409 converging`
  - migrating hosts: `409 migrating`

---

## 2. Computer lifecycle

### Create: `POST /computers` (§Create computer, L14089)

Body fields from the spec: `workspace_id`\*, `name`\* (≤80, unique in workspace), `os`, `cpu`, `ram`, `disk_size_gb`, `resolution`, `gpu`, `template_ref`, `billing_mode`, `ends_in_hours`, `on_end` (plus undocumented `compute_quote_id`, `compute_request_id`). Header: `Idempotency-Key`.

| Field | Values | Default |
|---|---|---|
| `os` | `linux`, `windows`, `macos`, `android`, `ios` | `linux` |
| `ram` (GB) | 4, 8, 16, 32 (64 on request). macOS is fixed at 10 | 4, or the template's |
| `cpu` | 0.5, 1, 2, 4, 6 (8 on request; 16 → `403 PER_COMPUTER_CPU_CAP`) | vCPU matching the RAM: 4→1, 8→2, 16→4, 32→6 |
| `disk_size_gb` | 1 up to the plan's per-computer max | the template's disk, or 40 on current plans |
| `resolution` | `WxHxD`, e.g. `1280x720x24`, `1920x1080x24` | `1280x720x24`. **A template's resolution always wins** (L6162) |
| `gpu` | `2q`, `4q` (Linux only) | none. May silently downgrade (`gpu_downgraded`) |
| `template_ref` | `namespace/name@version` | none. Not ready → `409 TEMPLATE_NOT_READY` |
| `billing_mode` | `plan`, `hourly` | `plan`. Past room, a full-access key auto-falls back to hourly. `"plan"` forces the refusal instead (L14126-14135) |
| `ends_in_hours` | 1-720 | none. **At the end the computer is deleted**, never just stopped (§Set when a computer ends, L3038) |

- **Instance types** (§Instance types, L4034-4040). There are no names in the API, only `cpu`/`ram`/`disk_size_gb`:

  | Size | vCPU | RAM | Disk |
  |---|---|---|---|
  | Small | 0.5 | 4 GB | 20 GB (API only) |
  | Medium | 2 | 8 GB | 40 GB |
  | Large | 4 | 16 GB | 80 GB |
  | XL | 6 | 32 GB | 150 GB |
  | 2XL | 8 | 64 GB | 300 GB (on request) |

- **Response:** `201`, returned once the computer is `running`.
  - Fields: `id` (UUID, **your stable handle**), `instance_id` (changes on start-from-archive or wake-from-storage), `status`, `os`, `cpu`, `ram`, `resolution`, `url` (internal fleet address, don't use), `hostname` (`www.orgo.ai`), `connection_url` (`https://www.orgo.ai/desktops/{instance_id}`), `vnc_password` (**rotates on restart, start and RAM resize**; never persist it), `instance_details`, `hardware`, `launch`, `ends_at`, `prepaid_*` (L14161-14241).
  - Fast path: poll `GET https://www.orgo.ai/api/desktops/{instance_id}/proxy/health` until `200`, then connect (L14244).
- **Latency:**
  - Cold create: "about fifteen seconds" (§Introduction, L33), elsewhere "online in seconds".
  - Template create: restores the golden snapshot and "boots far faster" (L14098). It cold-boots instead if the launch injects secrets (§Templates, L5772).
- **Main errors:** `403 VM_SLOT_ADDON` (computer limit), `RAM_ADDON`/`VCPU_ADDON` (pool, with `hourly_available`), `CONTACT_US_FOR_SIZE`, `402 insufficient_credit`, `409 NAME_TAKEN`/`FITS_PLAN`, `422 OS_UNAVAILABLE`, `503 FLEET_CAPACITY_FULL`/`TEMPLATE_HOST_UNAVAILABLE` (both `retryable: true`), `429 rate_limited` (L14416-14473).

### Get: `GET /computers/{id}` (§Get computer, L14494)

- `id` accepts the UUID or `instance_id`.
- Key fields: `status`, `fly_instance_id` (the current instance id, under its legacy name; `null` when frozen or asleep in storage), `connection_url`, `vnc_password`, `private_screens`, `asleep { where: host|storage, since, reason, waking{since,expected_seconds}, wake_error }`, `suspension {…}`, `lock`, `ends_at`, `on_end`, `deletes_at`, `prepaid_until`, `hardware`, `permissions`.
- `vnc_password` is `null` for Can chat users, restricted keys without Control write, MCP sign-ins and identity tokens (L14556-14557).

**Status values** (L14601-14613):

| Status | Meaning |
|---|---|
| `creating` | Being provisioned |
| `running` | Up and reachable |
| `restarting`, `updating` | Restart or in-place OS update in flight |
| `suspending`, `suspended`, `resuming` | Sleep with memory saved, on its host or in storage |
| `frozen` | Stopped: disk archived, no host held |
| `stopped` | VM gone from the host or terminal. Startable |
| `error` | Host error after a restart or update. Startable |
| `deleted` | Tombstone |

### Power operations

| Op | Endpoint | Behaviour |
|---|---|---|
| Start | `POST /computers/{id}/start` (`?async=true` or body `{"async":true}` or `Prefer: respond-async`; optional `ends_in_hours`/`ends_at`) | `frozen` → restores the disk and **cold-boots, possibly on another host**, with a new `instance_id`, address, ports and VNC password. Memory is gone. A suspended-in-storage computer wakes with memory "in about a minute". Synchronous mode waits up to 300 s, then returns `202` + `poll_url` (§Start computer, L16494-16720). Idempotent for `running` |
| Stop | `POST /computers/{id}/stop` | Archives the disk and releases the host. Result: `frozen`. Linux/Windows only. Memory lost (§Stop computer, L16730-16754) |
| Restart | `POST /computers/{id}/restart` | Reboots in place. Keeps ports and VNC password. WebSockets drop. `409 stopped` if frozen (§Restart computer, L16839-16844) |
| Suspend | `POST /computers/{id}/suspend` | Saves memory and disk. Linux, no GPU. `200 suspended` or `202 suspending`. May be moved to storage. **Hourly computers → `409 HOURLY_USE_STOP`** (§Suspend computer, L16957-17030) |
| Resume | `POST /computers/{id}/resume` (optional end) | On the host: back "in seconds", `200 running`. From storage: `202 {pending, waking, expected_seconds}`, about a minute, new `instance_id` (§Resume computer, L17049-17087). **Any action (click, bash…) also wakes** a suspended computer (L14608). Hourly asleep-in-storage → actions return `409 resume_required` |
| Suspension status | `GET /computers/{id}/suspension[?after=resume]` | `status` plus `suspension {state: capturing\|uploading\|stored\|resuming\|resumed\|failed\|abandoned, kind, bytes_done, bytes_total, progress, where, saving, …}` (L17275-17330) |
| Operation poll | `GET /computers/{id}/operations/{operationId}` | `status`: `queued\|running\|succeeded\|failed\|needs_review`. Poll every 2 s. `needs_review` settles within about 2 h, and new start/stop gets `409 OPERATION_NEEDS_REVIEW` until then (L17418-17426) |
| Auto-sleep | `PATCH /computers/{id}/resize` with `auto_stop_minutes` (0 = always on, max 43,200) | "Idle minutes before the computer suspends itself" (L15482). Dashboard choices: 15 m … 4 h (L218). **Template and clone/fork launches are forced always-on** (L6177, L15073). The idle definition is undocumented **[risk]**. Also `GET/PATCH /api/computers/{id}/auto-stop` **[perm-table]** (L9151) |

### Ends, extend, update

- `PATCH /computers/{id}` takes `name`, `bot_mascot`, `ends_at` (≤720 h or `null`) or `ends_in_hours`. Renaming needs workspace **owner** (§Update computer, L14715-14741).
- `POST /computers/{id}/extend {hours: 1-2160}` adds to an existing end, at most 90 days out. **`Idempotency-Key` required** (§Extend computer, L14938-14963).
- **For 9AM9AM Bots, never set an end on a user box.** At the end it is deleted with everything on it.

### Copy, move, resize, delete

| Op | Endpoint | Notes |
|---|---|---|
| Clone | `POST /computers/{id}/clone {name?, workspace_id?}` | Disk copy. Fresh boot, new UUID. Source must not be `frozen` (`400 NOT_CLONABLE`). Copy is forced always-on. Counts as a new computer (§Clone computer, L15070-15111) |
| Fork | `POST /computers/{id}/fork {workspace_id?}` | Live copy with memory, from a running Linux source. Source pauses about 2 s (§Fork computer, L15214-15228) |
| Move | `PATCH /computers/{id}/move {workspace_id}` | Must own both workspaces. Workspace keys can never move (§Move computer, L15347-15361) |
| Resize | `PATCH /computers/{id}/resize {vcpus?, mem_gb?, disk_size_gb?, bandwidth_limit_mbps?, auto_stop_minutes?}` | Running computers only. One at a time. `200`/`207`/`422` with `results[]`. RAM is live on supported Linux, otherwise a restart. Live RAM caps at 32 GB. vCPU above boot threads needs a restart. `connection_rotated`/`identity_changed` → re-fetch the password and ports. **Hourly computers can't change CPU, RAM or disk** (`409 COMPUTE_HARDWARE_PINNED`) (§Resize computer, L15455-15626) |
| Resize limits | `GET /computers/{id}/resize` | `max_disk_gb`, `max_ram_gb`, `max_cpu`, `vcpu_threads`, `live_mem_resize`, `past_xl_allowed`… (L15637-15688) |
| Delete | `DELETE /computers/{id}` | Permanent. Best-effort host teardown. `409` while migrating, waking, suspending or evicting (§Delete computer, L15739-15816) |

### Health and heal (documented, but not in the OpenAPI spec)

- `GET /computers/{id}/health` changes nothing. Returns `overall: healthy|degraded|stopped` and `checks[]` for `status, agent (Desktop API), exec, display, vnc` (§Check computer health, L15969-16002).
- `POST /computers/{id}/heal {action: auto|restart}` returns `healed`, `steps[]`, `health`, `recommendation`, `code`, `retry_after` (§Heal a computer, L16035-16082).
- Stuck-computer runbook: health → heal → heal restart → support (§Troubleshooting, L4980-4985).

### Other lifecycle bits

- **Backups:** daily for running Linux/Windows computers, kept 7 days (§Compare platforms, L5019). `GET /api/computers/{id}/backups` and `POST …/backup/restore` exist **[perm-table]** (L9153-9154).
- **Usage:** `/api/obs/{id}` (live CPU, memory, disk) and `POST /api/computers/usage` (up to 100 computers) **[perm-table]** (L9145, L9196).
- **Persistence:** disk survives stop and restart. Memory survives only suspend (§Instance types, L4177).
- **Free trial credit:** when it runs out, files are kept 7 days (L807).

---

## 3. Computer actions

All action endpoints take the computer **UUID** in the path. An `instance_id` fails with `500` on the action routes (L17441). The fixed timeout is 30 s for screenshot, click, type, key, scroll, drag and mouse-move (§Computer actions and timeouts, L2829-2834).

| Action | Endpoint | Body / query | Notes |
|---|---|---|---|
| Screenshot | `GET /computers/{id}/screenshot` | `screen`, `response_format=url\|base64\|binary`, `format=png\|jpeg\|webp`, `quality` 1-100 (80), `scale` (0,1], `image_encoding=data_url` | Default `url` returns a relative `/api/storage/...` path that **needs auth**, and workspace keys can't fetch it. Use `base64` (`{success,image,mime_type,width,height}`) or `binary`. Linux captures PNG. Doesn't wake an asleep computer (`409 computer_asleep`) (§Take screenshot, L17436-17569) |
| Click | `POST /computers/{id}/click` | `x`, `y`, `button` left/right/middle, `double`, `modifiers[]` (ctrl/alt/shift/super, up to 4), `repeat` 1-10 (3 = triple-click) | Missing x/y silently clicks at 0. `double` always uses left. Modifiers can't combine with double or repeat (§Click mouse, L17594-17643) |
| Mouse move | `POST /computers/{id}/mouse-move` | `x`, `y` (required, ≥0) | Hover. **Each screen has its own pointer** (L17802) |
| Drag | `POST /computers/{id}/drag` | `start_x`, `start_y`, `end_x`, `end_y` | Left button, one straight motion, no speed control (L17924) |
| Type | `POST /computers/{id}/type` | `text`, `delay_ms` (default 12) | Unicode OK. **Anything over about 2,500 chars at default speed hits the 30 s cap** → `503 ECONNABORTED`, possibly half-typed. Split it. Missing `text` returns `200` and types nothing (L18063-18095) |
| Key | `POST /computers/{id}/key` | `key` | **X11 keysym names, case-sensitive**, combos joined with `+`: `Return`, `BackSpace`, `Tab`, `Escape`, `Delete`, `space`, `Up/Down/Left/Right`, `Home/End`, `Page_Up/Page_Down`, `F1-F12`. Modifiers: `ctrl alt shift super`. Bad key → `500`, not `400` (L18212-18248). Lowercase single letters, or `ctrl+C` also presses Shift (§Gemini, L7894-7896) |
| Scroll | `POST /computers/{id}/scroll` | `x`, `y`, `direction` up/down, `amount` (wheel clicks) | **Always send x,y.** Omitting them warps the pointer to (0,0). Vertical only. About 100 px of model `scroll_y` per click (L18356-18404, L7702) |
| Wait | `POST /computers/{id}/wait` | `seconds` (0-60] | No `screen`. Not supported on Windows (L18501-18527) |
| Bash | `POST /computers/{id}/bash` | `command`, `timeout` (default **200 s**, clamped 1-300; `0` → 10), `?screen=` | Synchronous only, no job ids. Linux env: `HOME=/root`, `DISPLAY=:99`, `PATH=/usr/local/bin:/usr/bin:/bin`. With `?screen=screen-100` Orgo prefixes `export DISPLAY=:100;`. Returns `{success, command, output (stdout+stderr), exit_code}`. Timeout gives `exit_code -1` with truncated output, or `504 timeout` if the proxy hop expires (proxy waits timeout+30 s). Non-zero exit is still `200`. Windows runs PowerShell (§Execute bash, L18621-18764) |
| Exec (Python) | `POST /computers/{id}/exec` | `code`, `timeout` (default **10 s**, 1-300) | Fresh `python3 -c` each call on `DISPLAY=:99`. **No `?screen=`**: always the default display (L18808). `success` false when the code raises, and the traceback is only in `output`. `timeout` field is always false (L18788-18836) |

**bash vs exec:**

| | bash | exec |
|---|---|---|
| Runs | a shell command | a Python snippet |
| Default timeout | 200 s | 10 s |
| Targets a screen | yes, `?screen=` | no, always `:99` |
| `success` means | the command ran | the code exited 0 |

Neither documents an output-size cap beyond the general 10 MB body rule **[inferred]**. The Orgo-hosted agent's own shell commands stop at 120 s (L2823).

**No mouse-down/up, hold-key, cursor-position or zoom endpoints** exist. Claude's toolset members like `left_mouse_down` or `hold_key` have no mapping (§Claude Computer Use, L7352-7363). An in-VM host can do them with `xdotool` directly **[inferred]**.

**Coordinates:** send screenshots unresized and the model's pixel coordinates are the computer's pixels. The default Linux resolution is 1280×720. `GET /computers/{id}/screens` and base64 screenshots report the real `width`/`height` (§Use any model, L3280-3296).

### WebSockets

All take `?token=<vnc_password>`, the computer password from `GET /computers/{id}/vnc-password` or `vnc_password`. **Not** an API key in an `Authorization` header: that is accepted but not forwarded, and the socket closes `1000`. An `sk_` key sent from a browser is refused with `4001`. A `?token=` from a browser for an identity token isn't allowed either (L4534).

| Socket | URL | Protocol |
|---|---|---|
| Terminal | `wss://www.orgo.ai/desktops/{id}/ws/terminal?token=…&cols=80&rows=24&session=<name>` | JSON text frames. Client sends `{type:"input",data}`, `{type:"resize",cols,rows}`, `{type:"ping"}`. Server sends `{type:"output",data}`, `{type:"error",message}` (then `1000`), `{type:"exit",code}`, `{type:"pong"}`. Non-JSON frames are written raw. **`session` (1-32 `[A-Za-z0-9_-]`) makes the shell survive disconnects.** Idle close `4008` after 30 min; traffic either way counts. Terminal input counts as control calls, and going over closes `4029` (§Terminal WebSocket, L18958-19396) |
| Audio | `…/ws/audio?token=…&sample_rate=24000&channels=1` | Server sends `{type:"started",sample_rate,channels}`, then binary s16le PCM in 20 ms frames (960 B at defaults), plus `pong` and `error`. Client sends `{type:"stop"}` and `{type:"ping"}`. Server audio doesn't count as activity: idle `4008` after 30 min unless you send pings or other sockets are active (§Audio WebSocket, L19407-19739). Whole-computer, no per-screen audio **[inferred from no `screen` param]** |
| Events | `…/ws/events?token=…` | Send `{type:"subscribe",event_types:[…]}`. Types: `window_focus`, `window_open`, `window_close`, `clipboard` (≤1024 B, `truncated`), `screen_change`, `audio_stream_start/stop`, `process_start/stop`, `idle`/`active` (30 s without user input), `file_change` (**not delivered on Linux**). Frames look like `{type:"event",event:{type,timestamp,data}}`. Names aren't validated. Nothing is replayed. 256-event buffer, then drops. **No idle timeout.** Needs write access (§Events WebSocket, L19766-20264) |
| VNC | `…/ws/websockify?token=…&screen=<id>` | RFB over websockify (see §5) |
| WebRTC / stats | `…/ws/rtc`, `…/ws/stats` | Listed in the permission tables only (L4527-4528). Protocol undocumented. `POST /api/computers/{id}/webrtc` toggles UDP streaming **[perm-table]**. Close `4010` = WebRTC disabled |

Shared close codes: `4001` auth, `4003` not running or asleep, `4004` no computer, `4006` wrong-workspace key, `4007` MFA, `4008` idle, `4012` no such screen, `4500`/`4502`/`4503` proxy (retry), `1001` draining (reconnect).

---

## 4. Screens API (critical: one screen per Bot)

### What a screen is

- **Separate X servers:** each screen is "its own X server, with its own root window, its own cursor, and its own window manager. An agent working on one screen cannot disturb another" (§List screens, L20291-20293). "X11 has a single pointer per display, so two agents sharing one screen are two hands on one mouse" (§Create a screen, L20413).
- **Max 4 per Linux computer,** including the boot screen (L20417). The 5th gets `409` "this VM already has 4 screens, which is the limit" (L20523).
- **Memory, not the count, is the real limit:** "a computer runs out of memory long before it runs out of screens" (L20418).
- **Linux only:** macOS, iPhone and Android have exactly one screen. **Windows has no screens endpoints** (`404`).
- **Bare by design:** a created screen gets **only a window manager**: no panel, wallpaper or icons (L20468).

### Screen object

```json
{ "id": "screen-100", "display": ":100", "width": 1920, "height": 1080,
  "vnc_port": 6000, "ws_port": 6081, "default": false }
```

| Field | Rule |
|---|---|
| `id` | Boot screen is always `default`. Created screens are `screen-<display number>` |
| `display` | Boot screen `:99`. New screens take the lowest free number from `:100` up, so freed numbers are reused |
| `vnc_port` | 5900 + display |
| `ws_port` | 5981 + display |

So `:99` → 5999/6080 and `:100` → 6000/6081 (L20346-20363).

### Endpoints

| Op | Endpoint | Notes |
|---|---|---|
| List | `GET /computers/{id}/screens` | Boot screen first. Asleep with screens on file → answered from the record. Asleep with none on file → `409 computer_asleep` |
| Create | `POST /computers/{id}/screens {width?, height?}` | `201` + screen. Send both dimensions or neither. **If either is missing or 0, both fall back to the boot size** (L20440-20444). Empty body = match the boot screen |
| Get | `GET /computers/{id}/screens/{screenId}` | The **reliable existence check**: `404` for an unknown id. Never falls back to `default` |
| Resize | `PATCH /computers/{id}/screens/{screenId} {width, height}` | `xrandr` in place, windows kept, ports unchanged. Unknown id → `500 no screen "<id>"` (not 404) |
| Destroy | `DELETE /computers/{id}/screens/{screenId}` | `204`. Kills the X server, WM and every window on it. `default` → `409`. Unknown id → `409` (not 404) |

The OpenAPI spec confirms the create body is only `width`, `height`. Screens endpoints count as "other API calls" for rate limits (`api_other_per_minute`), not control calls (L20383).

### Targeting a screen

- Add **`?screen=<id>`** to screenshot, click, mouse-move, drag, type, key, scroll and bash (L20451-20461).
- **No `screen` param means the boot screen.** An unknown id returns `404` and never falls back.
- `wait` and `exec` ignore `screen`. For Python on a bot screen, use bash with `?screen=`, or set `DISPLAY` yourself.
- Inside the VM, the same thing is just `DISPLAY=:100` etc. **[inferred]** Orgo's bash prefix literally does `export DISPLAY=:N` (L18641-18644). So an in-VM agent host can launch headed Chrome or Playwright per bot with `DISPLAY=:10N` and drive it with `xdotool`/`scrot` without Orgo API calls.
- **Viewer:** `wss://www.orgo.ai/desktops/{id}/ws/websockify?screen=screen-101&token=…`. Omit `screen` or send `default` for the boot screen. An unknown screen closes with `4012` (§Embed computers, L4720-4728).
- **Orgo's hosted chat-completions agent can't target a screen** (no parameter in the spec). **[inferred]** It acts on the boot screen only.

### Persistence (§List screens, "Screens after a restart", L20301-20331)

- **Recreated after restart and start:** Orgo records extra screens (ids and sizes) on every create, resize and destroy. It recreates them after a restart, a start (including from `frozen`), or a resize that restarted, **with the same ids, displays and ports**.
- **But empty:** windows are gone. It's best effort, runs in the background for up to 3 minutes, and skips screens it can't make.
- **Not recreated** after an OS update, a backup restore, an in-VM `reboot`, or a plan-downgrade stop. The boot screen's size isn't recorded either.
- **Suspend/resume:** memory is restored, so open windows and processes on every screen should survive **[inferred]** (resume "picks up exactly where they were", L16957).

### Implications

- Per user: `default` + up to 3 extra screens. Practical options:
  - (a) `default` = Main Bot, extras = 3 more bots.
  - (b) Keep `default` for the human, the home desktop with panel and icons, and give bots `screen-100..102`.
- Each screen with a Chrome instance costs about 1-2 GB of RAM **[inferred]**. Size the box at **Large (16 GB)** for 3-4 browser-heavy bots. Medium (8 GB) is tight.
- **More than 4 bots** need a screen pool (§10).

---

## 5. Viewing and embedding

### Connection (§Embed computers, L4677-4830)

- **URL:** `wss://www.orgo.ai/desktops/{id}/ws/websockify?token={password}[&screen=<id>]`. Use the computer **UUID** in the path. `instance_id` also works but changes on wake.
- **Password:** `GET /computers/{id}` → `vnc_password`, or `GET /computers/{id}/vnc-password` → `{password, desktop_api_token}`.
  - The vnc-password route takes **UUID only**; an instance id gives `500`.
  - Send `desktop_api_token` (usually equal to `password`) to the Desktop API, and `password` to the WebSockets (§Get VNC password, L15838-15842).
  - Fetch it per session: it rotates on restart, start, RAM resize, and revocation of a controlling one-computer key. While rotating, the endpoint answers `503 vnc_rotation_pending`.
- **Who can read it:** owner and Full-access members. Can chat members and MCP sign-ins can't. A workspace key can for its own workspace (L15846).
- **No origin allowlist.** The per-computer password is the only credential (L4730-4732).
- **Clients:** noVNC ≥1.7.0 (`import RFB from '@novnc/novnc'`), with `credentials: { password }` and `credentialsrequired` → `sendCredentials`. Or the React package `orgo-vnc` ≥0.3.0 `<ComputerDisplay instanceId={id} password={…}/>` (`instanceId` accepts the UUID). 0.2.x is broken (L4761-4830).

### Security model (the important part)

- **The VNC password is root on that computer.** The same value opens `/ws/terminal` and `POST /api/desktops/{id}/proxy/bash` (L4707). Never ship it to an untrusted client or put it in a bundle.
- **An `sk_` key in a browser** is rejected (`4001`). Never put API keys client-side (L4712).
- **There is no view-only password.** `rfb.viewOnly = true` is client-side only and a visitor can flip it. Server-enforced view-only exists only for a person **signed in to Orgo** with Can chat access (L4716-4718). "An anonymous embed always has full control of the computer."

**9AM9AM Bots recommendation [inferred design]:** run a **WebSocket relay on our server** at `wss://9am9am/api/computer/{botId}/stream`.

1. Authenticate our user with our own session.
2. Fetch the VNC password server-side with the account key.
3. Open the upstream `wss://www.orgo.ai/desktops/{id}/ws/websockify?screen=…&token=…`.
4. Pipe frames.
5. In **Watching** mode, drop client→server RFB input messages: KeyEvent (type 4), PointerEvent (type 5) and ClientCutText (type 6). Pass SetPixelFormat, SetEncodings and FramebufferUpdateRequest.
6. In **Take control** mode, pass everything.

This keeps the root-equivalent password off the browser and enforces view-only on the server. Mind the 16 MB max VNC message (L2964).

### Idle behaviour

- **Every embed eventually hits `4008`** after 30 min with no *human* input (keyboard, pointer or clipboard from the page). An agent driving the screen doesn't count (L4811-4812). Treat it as a clean close and reconnect on user activity. With a relay, re-dial upstream transparently.
- **Black screen debugging:** rejections accept the upgrade, then close. Read the close code from the socket; noVNC doesn't surface it (L4789-4809). The codes are listed in §3.
- **Other viewers:** the dashboard has "Click to take over" (L204). Orgo's own app at `orgo.ai/computers/{id}` isn't embeddable for our users. `private_screens` on a workspace only affects Orgo's dashboard (L14559).

---

## 6. Orgo's hosted computer-use agent (`/v1/chat/completions`, threads)

### Request (§Create chat completion, L12589-13121)

```http
POST https://www.orgo.ai/api/v1/chat/completions
Authorization: Bearer sk_live_…
X-OpenAI-Key: …        (optional; pays for orgo-agent)
X-Anthropic-Key: …     (optional; pays for claude-* and orgo-agent-opus-5.5)
{ "computer_id": "<uuid|instance_id>", "messages": [...], "model": "orgo-agent",
  "stream": false, "max_steps": 250, "thread_id": "<uuid>" }
```

- **messages:** only `user` and `assistant` roles. **`system` messages are dropped.** Content is a string or parts `text` / `image_url` (https or a base64 data URL, user messages only).
- **Models** (L12683-12694):

  | Model | What it is |
  |---|---|
  | `orgo-agent` (default) | Orgo's engine on OpenAI GPT-6.1 Terra, fast tier |
  | `orgo-agent-opus-5.5` | Same engine on Claude Opus 5.5 |
  | `claude-sonnet-5` | Default on Android |
  | `claude-opus-5.5`, `claude-opus-5`, `claude-opus-4.8`, `claude-sonnet-4.6`, `claude-opus-4.6` | Pinnable Claude models |

  Anything else → `400 invalid_model`.
- **Steps:** 250 by default, `max_steps` up to 500. It can raise the limit, never lower it. Hitting the limit → `finish_reason:"length"` (L12717-12785).
- **Response:** an OpenAI `chat.completion` with **only the final assistant text**, plus `usage` and `orgo {thread_id, steps, cost_cents, own_key, credit_balance_cents}` and headers `X-Request-Id`, `X-Thread-Id`.
- **Streaming:** SSE `chat.completion.chunk`, **text only**. "Screenshots, clicks, and commands run on Orgo's side and are not sent." Closing the stream stops the run at its next step (L12922-12925). Mid-stream errors arrive as one `data: {"error":…}` event.
- **Threads:** `thread_id` **loads no history and replaces the stored transcript**. Resend the full transcript every turn (L13004-13012).
  - `/v1/threads`: list (`?computer_id=`), create, get, delete. Account-wide key only. Creator-only reads.
  - `/chat/threads` is the dashboard's surface.
- **Billing:** workspace owner's AI credits, at provider list price plus 10% markup. A 10¢ hold per request. Stops 25¢ before the balance runs out. A member's requests are capped at $5 (L13061-13070, L2815-2818).
- **Rate limits:** agent runs per minute and concurrent (Scale 120/min, 50 at once).
- **Kill switch:** an account can have the endpoint disabled (`403 model_proxy_disabled`).

### Can we plug in Grok?

**No.** "The chat endpoint takes keys for OpenAI and Anthropic only. There is no header for Google, xAI, DeepInfra, or any other provider. For any other provider, run the agent loop yourself… Orgo bills no AI credits on that path… your prompts and the model's replies never reach Orgo" (§Bring your own model key, L3719-3723). BYOK keys still flow through Orgo's servers (L3705-3711). MCP and the CLI can't pass BYOK headers (L3715-3717).

**Verdict for 9AM9AM Bots:** don't use `/v1/chat/completions` for bots. It has no xAI support, no screen targeting, opaque tool calls (nothing to drive our activity line or click overlay), and a concurrency cap.

**Use instead:** our own Grok loop against Orgo primitives, or xdotool/scrot inside the VM. The "run the loop yourself" pattern (§Use any model, L3273-3528):

1. Screenshot (`response_format=base64`).
2. Send it to the model.
3. Map the returned actions to click, type, key, scroll, drag and bash.
4. Repeat.

The OpenAI-guide keysym map (L7581-7593) is a good template for normalising model key names: `ENTER`→`Return`, `BACKSPACE`→`BackSpace`, `CTRL`→`ctrl`, `CMD`/`META`→`super`, single letters lowercased.

---

## 7. Files, Secrets, Templates

### Files (§Upload file … Delete file, L20785-21651)

| Op | Endpoint | Key facts |
|---|---|---|
| Upload ≤10 MB | `POST /files/upload` multipart: `file`, `workspaceId`\*, `desktopId?`, `destPath?` | **Without `destPath`:** stored in the workspace and **pushed to `/root/Desktop` of every computer running in that workspace**. Existing names are not overwritten, the push is best-effort and in the background, and stopped computers miss it (L20918-20923). **With `destPath`** (absolute path, or `desktop`) plus `desktopId`: placed on that one computer only, no record, response `{ok, placedIn}`. Too big → `400 File too large (max 10MB)` |
| Start large upload ≤2 GB | `POST /files/uploads {desktopId, filename, size_bytes, destPath?}` | Returns `upload_url` (PUT exactly `size_bytes`, valid **15 min**) and `upload_token` (valid 1 h). Bytes bypass Orgo's API |
| Complete large upload | `POST /files/uploads/complete {desktopId, upload_token}` | The computer must be running. It pulls the file and **replaces** any file with the same name. Retryable with the same token |
| Export ≤2 GB | `POST /files/export {desktopId, path}` | Copies off a running computer into workspace storage (`source: export`). Returns `url` (signed, 1 h) |
| List | `GET /files?workspaceId=&desktopId=` | Newest first. `size_bytes` is a string |
| Download | `GET /files/download?id=` | Signed URL, 1 h, no auth needed to fetch |
| Delete | `DELETE /files/delete?id=` | Copies already pushed onto computers stay |

**For 9AM9AM Bots:**

- Always use `destPath` or large uploads to target one box. The plain upload leaks the file to every running computer in the workspace.
- Use export + download for "download file from bot".
- For big inbound data, have the VM `curl` it (no cap besides disk) (L5478).

### Secrets (§Secrets, L6448; §Apply secrets to a computer, L16115; §Save a secret, L16214; §Apply secrets to many computers, L16268)

- **Vault:** account-wide or per-workspace. A workspace value overrides the account value of the same name. Values are encrypted and **never returned**.
  - Name: ≤64 chars `[A-Za-z0-9_-]`, saved upper-cased with `-`→`_`. Value: ≤8,192 chars.
- **Save:** `POST /api/secrets {name, value, workspace_id?}` → `{ok:true}`. Needs an account-wide key (not in the OpenAPI spec).
- **Apply to one computer:** `POST /computers/{id}/secrets/sync {names[1-50], wake=true, remove=false, dry_run=false}`.
  - Running Linux only. At most 128 KiB per call (`413 too_large`). `ORGO_API_KEY` is reserved (`422`).
  - **Writes `/root/.orgo-secrets.json` and `/root/.orgo-secrets.env` (mode 0600, root)**, adds a loader line to `/root/.env`, `.bashrc` and `.profile`, and fills known agents' env files (Hermes, OpenClaw, Codex).
  - **Running processes keep their old environment.** Re-read the file or restart the service (L16121-16210).
- **Apply to many:** `POST /secrets/apply {names, computer_ids[≤100]?, workspace_id?, remove?, dry_run?}`.
  - **Never wakes computers.** Asleep → `not_changed`.
  - Works 4 computers at a time for about 45 s. Then returns `remaining[]` and `next[]` bodies to resend (L16274-16403).
- **Removal:** with `remove: true`, removal uses HMAC matching so the VM never sees vault values it didn't already have (L16159-16161).
- **Saving or deleting a vault entry doesn't change running computers** (L16210).
- **Template-declared secrets** are injected at create into `/root/.env` under `UPPER_SNAKE` names. **This forces a cold boot instead of the warm golden-memory restore** (L6555-6559).

**For 9AM9AM Bots's connector tokens [inferred]:** Orgo secrets work but are coarse. Any process on the box (every bot, plus anything a bot runs) can read `/root/.orgo-secrets.env` as root.

- Prefer keeping OAuth tokens on our server, with the in-VM host requesting short-lived tokens or proxying the API calls.
- Use Orgo secrets only for per-box bootstrap values, if at all.

### Templates (§Templates, L5729; §Templates quickstart, L5841; §Template schema, L6049; §Triggers, L6598; §Examples, L6737; Templates API, L21655-22843)

- **Golden snapshots:** a template is a declarative `orgo.ai/v1` YAML or JSON document. A build boots a VM, runs the install steps, then captures **disk, memory and CPU** as a golden snapshot keyed by a SHA-256 `digest`. Launch = copy + resume, so it's seconds. It cold-boots from the snapshot disk instead if secrets are injected or the host can't restore memory (L5761-5772).
- **Refs:** `namespace/name@version`, semver, **immutable**. Iterate on one version with `?force=true`. Yours live in `default`. Curated ones are `system/…` (Coding `system/coding@1.0.4`, `system/openclaw@1.1.9`, `system/hermes-agent@1.1.16`).
- **Plans:** publish, build and test-run need **Scale+**. Launch needs any paid plan.

Schema highlights (canonical form; sugar form also accepted):

| Field | Key points |
|---|---|
| `hardware` | `cpu` (1, 2, 4, 8, 16), `ram_gb` (4, 8, 16, 32, 64), `disk_gb`, `resolution` (always wins over the request), `audio`, `bandwidth_mbps`. `auto_stop_minutes`, `gpu` and `region` are **accepted but ignored** |
| `build` | `apt[]`, `pip[]`, `npm[]`, `run[]` (≤64 commands), baked into the snapshot |
| `apps[]` | `install` script, `services[]` (`run`, `cwd`, `user` (default `orgo`, unprivileged), `restart: always\|on-failure\|no`, `env`, `log`), managed by **supervisord** (start at boot, respawn on crash). `health {type: http\|tcp\|command\|process, every, timeout, retries, on_fail: restart_service:<name>}` (only `restart_service` acts). `ports[] {internal, public}`: reserved ports 5900/5999/6080/8080. A public port maps to a host port that only test-run reports, **not Get computer**. `autostart[] {run, delay}` launches GUI apps once the desktop is up |
| `files[]` | `to` (absolute path, no reserved paths: `/proc /sys /boot /dev /tmp /run /etc/orgo /var/orgo /orgo /etc/supervisor`), `from` (http, https, git, public s3) or `inline`, `mode`, `owner`, `when: build\|runtime` |
| `env` | Literal values written to `/root/.env`, or `{secret: name}` |
| `secrets[]` | Names only. `orgo_api_key` makes Orgo issue a computer key |
| `terminal[]` | Detached tmux sessions, recreated on cold boot, **not auto-restarted** |
| `hooks` | **Only `on_first_boot` (build only) and `on_every_boot` (cold boots) run.** `on_resume` and `on_shutdown` never run (L6366) |
| `triggers[]` | Sources `cron`, `file`, `http`, `process`, `metric`, `log`, `event` (via `/var/lib/orgo/events.jsonl`), `desktop` (some event names never fire). Actions `webhook`, `command`, `service`, `notify`, `log`, `api` (no token, so authed routes 401). Dedup and a 120 fires/min cap |
| `egress_policy` | `allow`/`block` with domain, IP or CIDR rules (domain rules filter DNS only) |
| `telemetry` | OTLP metrics and logs to your own endpoint |
| `streaming` | Parsed but doesn't broadcast |

**API:**

| Op | Endpoint |
|---|---|
| Schema (public) | `GET /template-schema` |
| Validate (no side effects; `422 {errors[{field,code,message,hint}]}`) | `POST /templates/validate` |
| Publish (`Content-Type: application/yaml`) | `POST /templates?auto_build=true&force=true` |
| Build (`?tier=standard\|fast\|turbo&launch=true&project=`) | `POST /templates/{ns}/{name}/{ver}/build` |
| Build status (`not_built\|building\|ready\|failed`). Poll it: queued builds don't stream logs | `GET …/build` |
| Cancel build | `DELETE …/build` |
| Build logs (registry-host builds only) | `GET …/build/events` (SSE) |
| List / versions / get / delete | `GET /templates`, `GET /templates/{ns}/{name}`, `GET/DELETE /templates/{ns}/{name}/{ver}` |
| Curated | `GET /templates/global[/{ns}/{name}]` |
| Test-run (auto-reaped preview; returns `template_ports`) | `POST /templates/run {ref}`, `DELETE /templates/run?id=` |

**Sketch of a "9AM9AM Bots box" template [inferred, adapt]:**

```yaml
api_version: orgo.ai/v1
template: { name: 9am9am-box, version: 1.0.0, description: 9AM9AM Bots per-user box }
hardware: { cpu: 4, ram_gb: 16, disk_gb: 80, resolution: 1280x800x24, audio: true }
build:
  apt: [xdotool, scrot, imagemagick, jq, ffmpeg, x11-utils, tmux]
  run:
    - curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
    - npm i -g pnpm && npx -y playwright@1.49.1 install --with-deps chromium
    - mkdir -p /opt/9am9am-host && touch /var/lib/orgo/events.jsonl
files:
  - { to: /opt/9am9am-host/host.tgz, from: "https://releases.9am9am.internal/host-1.0.0.tgz" }
apps:
  - name: 9am9am-host
    install: tar xzf /opt/9am9am-host/host.tgz -C /opt/9am9am-host && cd /opt/9am9am-host && pnpm i --prod
    services:
      - name: 9am9am-host
        run: node /opt/9am9am-host/dist/main.js
        user: root              # needs X displays, /root/.orgo-secrets.env
        restart: always
        env: { BOTS_API: "https://9am9am.internal", NODE_ENV: production }
    health: { type: http, url: http://127.0.0.1:7801/healthz, every: 15s, retries: 3, on_fail: restart_service:9am9am-host }
egress_policy: { mode: block, rules: [] }   # or allowlist our API + model endpoints
```

- **No `secrets:`,** to keep warm golden-memory launches. Deliver a per-box bootstrap token after create (§10).
- **Pin a Chrome or Chromium version** in the build, and launch it per screen from the host with `DISPLAY=:10N`.

---

## 8. Workflows, Memory, Connectors, identity tokens, networking, scaling, clients, pricing

### Workflows (§Workflows, L2017; API L22845-24038). In preview: closed accounts get a bare `404`

- **Shape:** JSONL "lines". One `start` (time, app event, or "agent calls it"), then steps with `after` dependencies. Step kinds: `app`, `agent` (`hermes`/`claude-code`/`openclaw`), `script`, `screen` (Orgo's computer agent), `person` (Check with me), `message`, `chart`.
- **Runs on one computer.** It wakes it about 3 min before a timed run (L2103).
- **Endpoints:** `GET/POST /workflows`, `GET/PUT(If-Match)/PATCH/DELETE /workflows/{id}`, `POST /workflows/{id}/validate`, `GET/POST /workflows/{id}/runs`, `GET …/runs/{runId}`, `POST …/stop`, `POST …/answer` (key/agent may only `stop`; go needs a person), `GET /workflows/{id}/events` (SSE, closes after 5 min, max 10 streams per credential).
- **Permissions:** the `workflows` group, plus AI write and Control write for computer steps.
- **Verdict:** a parallel "routines" product tied to Orgo's built-in agents and Composio-backed connectors. **Not a fit for 9AM9AM Bots routines**, which should run on our own scheduler and agent **[inferred]**.

### Memory (§Memory, L4558)

Only a Mem0 recipe: search before a task, inject, store after. There's no Orgo memory API. 9AM9AM Bots memory stays in our DB and/or the box's disk.

### Connectors ("Connect apps to an agent", §Connectors, L1950-2013)

- **Dashboard-only:** connecting Gmail, Calendar, Drive, Notion, Slack and "More apps" (via **Composio**) is a dashboard flow for Orgo's installed agents (Hermes, OpenClaw, Claude Code, Codex).
- **Per computer:** connections belong to **one computer**, and deleting it signs them out. Anyone who can chat with that agent can use them.
- **No public connector API** beyond the workflow tools (`orgo_connect_app` returns a link to open, L1637).
- **For 9AM9AM Bots:** build connectors ourselves (our own OAuth, or Composio directly) **[inferred]**.

### Identity tokens (inside-VM auth) (§Get an identity token inside a computer, L4307-4554)

- **Mint:**

  ```bash
  curl -s -H 'Metadata-Flavor: Orgo' http://169.254.169.254/orgo/v1/identity/token
  ```

  Returns `{token:"orgo_vm_…", token_type, expires_in≈3599, expires_at, desktop_id, vm_id, user_id, project_id}`.
  - GET only, plain http on port 80. Not through a proxy: add `169.254.169.254` to `no_proxy`.
  - Same token while ≥40 min remain. `?fresh=1` after a `401 identity_stale`. Limit: 20 req/s per VM.
- **Scope:** the token acts as the computer's own key, **held to its own computer.** It can call get computer, bash, click, drag, exec, key, mouse-move, resume, screens list/create/get, screenshot, scroll, type, wait, the Desktop API proxy (all verbs), and `ws/audio|rtc|stats|terminal|websockify` (L4489-4530).
  - It **can't** read the VNC password, manage keys, see billing, destroy or resize screens, or delete the computer.
  - On the proxy and WebSockets Orgo swaps it for the computer credential, and connections are re-checked every 15 s (L4534).
- **Eligibility:** Linux, **workspace used only by its owner** (no members, invites, share links, workspace keys, Invoicing clients or MCP sign-ins), no one-computer key bound, and the owner not deleting (L4536-4543). Some computers or hosts return `404 not_enabled`, and the docs say "Soon…" in §Authentication (L8868). **Treat it as rolling out [risk].**
- **Not from browsers or containers:** web pages and Docker containers on the VM can't get one. Any process on the VM can (L4466, L4548).
- **Using it to auth to *our* server [inferred, not documented]:**
  1. The host presents its identity token to our API.
  2. Our server verifies it by calling `GET https://www.orgo.ai/api/computers/{desktop_id}` with that token as Bearer. A `200` proves it's live and bound to that computer.
  3. We then mint our own short-lived host session.

  Orgo documents no token-introspection endpoint. A simpler alternative: write a per-box secret onto the VM at provisioning (§10).

### Browser networking (§Browser networking, L4267-4303)

- **Bot checks:** VMs are in data centers, so sites may challenge them.
- **BYO HTTP CONNECT proxy:** `POST /api/computers/{id}/egress/upstream {mode:"custom", upstream:{host, port, user, pass, tls}}` → `{proxy_on, exit_ip}`. Revert with `{mode:"residential"}` (the default, i.e. direct). Read with `GET …/egress/upstream`.
  - It **controls browser traffic only, via Chrome policy.** First enablement restarts Chrome.
  - **[inferred]** Playwright-launched Chromium with its own profile may not pick up the policy. Pass `--proxy-server` yourself.
- **Also in the permission tables:** dedicated IPs `/api/egress-ips` and paired egress devices `/api/egress-devices` **[perm-table]** (L9159-9168).
- **Template `egress_policy`** covers allow/block filtering.

### Scale to thousands (§Scale to thousands, L4212-4263)

- **Limits:** 50 computers per account, then hourly from credit to 999. Contract up to 9999.
- **Pool:** buy capacity with `POST /account/capacity` (`dry_run`, `Idempotency-Key`, account-wide key; adds room, not computers). Read it with `GET /account/capacity` (`computers.{total,baseline,ceiling,can_raise,raise_available}`, `rates`, `can_add`).
- **Credit:** turn on auto top-up. Hourly computers stop when credit can't pay the next minute (L2606).
- **Create rates on Scale:** 60/min, 1,000/h, 5,000/day.

### Clients/workspaces: could each 9AM9AM Bots user be an Orgo client or workspace?

- **Workspace per user: yes, recommended [inferred].**
  - Up to 9,999 workspaces, more than the 999-computer cap.
  - Isolates files (the upload push), per-workspace secrets, and threads.
  - **Don't** create workspace-scoped keys or invite members, or identity tokens and computer keys are disabled for those boxes.
  - Creation is 120/h on Scale. Use one account-wide key on our server.
- **Invoicing client per user: possible, but not ideal for an internal app.**
  - `POST /v1/accounts {name, external_id, max_computers, monthly_ai_cap_usd}` creates a hidden workspace plus a scoped key (§Invoicing, L4841-4899; §Add a client, L11240).
  - Computers in client workspaces **count against your plan unless the client pays you** via Orgo billing (L2567).
  - Client workspaces make the box ineligible for identity tokens and computer keys (L4541, L10471).
  - They're hidden from `GET /workspaces`, so secrets must be saved with `workspace_id`.
  - Use only if we want hard per-user caps (`ACCOUNT_COMPUTER_LIMIT`, `account_ai_limit`).

### Pricing and instance types (§Plans and billing, L2289; §Computer prices, L2411)

| Plan | $/mo | Pool | Seats per workspace |
|---|---|---|---|
| Starter (`hacker_v2`) | 29 | 2 vCPU / 8 GB | 1 |
| Growth (`startup_v2`) | 99 | 8 / 32 GB | 5 |
| Scale (`scale_v2`) | 399 | 36 / 128 GB | 25 |
| Enterprise | custom | custom | — |

- **Monthly AI credit:** $5 / $10 / $50. Yearly saves 20%.
- **Plan add-ons:** RAM $2.50/GB/mo, vCPU $5/mo, disk $1/20 GB/mo, Windows $15. Per computer-equivalent:

  | Size | Monthly |
  |---|---|
  | Small | $13.50 |
  | Medium | $32 |
  | Large | $64 |
  | XL | $118 |
  | 2XL | $215 |

- **Hourly** (Linux only; billed per second; stopped computers pay disk only):

  | Size | Running | Stopped |
  |---|---|---|
  | Small | $0.023/h | $0.0014/h |
  | Medium | $0.055/h | $0.0027/h |
  | Large | $0.11/h | $0.0055/h |
  | XL | $0.202/h | $0.011/h |
  | 2XL | $0.368/h | $0.0205/h |

  A plan is cheaper above about 584 running h/month (L2464).
- **9AM9AM Bots math [inferred]:**
  - A Large box on plan capacity is $64/user/mo, fixed whether awake or asleep.
  - Hourly at 4 h/day ≈ 120 h × $0.11 + 600 h × $0.0055 ≈ $16.5/mo. But hourly can't suspend (only stop, so a cold boot on wake) and can't be resized.

---

## 9. MCP and CLI (brief)

- **MCP** (§MCP, L1475-1740):
  - Hosted at `https://www.orgo.ai/mcp`: OAuth (PKCE, 1 h tokens, scoped to one workspace, reviewed tool subset, no VNC passwords or secrets) or `Authorization: Bearer sk_…`.
  - Local: `npx -y orgo-mcp-server` with `ORGO_API_KEY`. Options: `ORGO_READ_ONLY`, `ORGO_TAGS`, `ORGO_DEFAULT_COMPUTER_ID` (hosted: `X-Orgo-Default-Computer-Id` header or `?computer_id=`).
  - One tool per operationId (`orgo_create_computer`, …). Retries only GET/PUT/DELETE. Hosted tool calls time out at 120 s (L2836).
  - Useful for our own dev agents, not for 9AM9AM Bots runtime.
- **CLI** (§CLI, L912-1285):
  - Install: `curl -fsSL https://orgo.ai/install.sh | bash` or `npm i -g orgo` (v2+ is CLI only; the TS SDK is `orgo@1`).
  - `orgo login` uses a device flow. Every command takes `--json`. `orgo manifest` dumps all commands.
  - Handy for ops: `orgo computers bash|exec|screenshot|view|vnc-password`, `orgo ssh <name>` (a WebSocket terminal, not real SSH), `orgo templates validate|publish --build`.
- **Device-approval key flow** (§For agents, L718-790): `POST /api/cli/auth/start` → `POST /api/cli/auth/poll`.
- **Direct SSH** (Linux): `POST/GET/DELETE /computers/{id}/ssh {public_key}`. Connect with `ssh -p <ipv4_port> root@<ipv4_host>`, never port 22 (§Connect with SSH, L1289-1388). Handy for debugging boxes.
- **SDKs:** Python `orgo>=0.0.47`, TypeScript `orgo@1` (≥1.0.22). Older versions default to 2 GB RAM, which the API rejects (L3278).

---

## 10. Recommended architecture for 9AM9AM Bots on Orgo

### Mapping

| 9AM9AM Bots concept | Orgo mapping |
|---|---|
| **User account** | One Orgo **workspace per user**, owned by our account. Our server holds the single full-access account key. Store `workspace_id` and `computer_id` (the UUID; never cache `instance_id` or `vnc_password`) |
| **Per-user box** | One Linux computer per user, `template_ref: default/9am9am-box@x.y.z`, size Large (`cpu:4, ram:16`). **No `ends_in_hours`.** Create with `Idempotency-Key`. Then poll `/api/desktops/{instance_id}/proxy/health` or wait for the host to dial in |
| **Box state machine** | `creating → running ⇄ suspending/suspended(host\|storage) → resuming → running`. Plus `frozen` via stop, and `error`/`stopped` → heal or start. Drive it from `GET /computers/{id}` (`status`, `asleep`, `suspension`). Poll `GET /computers/{id}/suspension` after `202`s |
| **Hibernate on idle** | Our server decides: no bot task running and no viewer connected for N min → `POST /suspend` (plan computers). **Don't rely on `auto_stop_minutes`.** Its idle definition is undocumented and could suspend mid-task. Set it to `0` (already forced always-on for template launches) |
| **Wake on presence** | When the user opens the app or a bot gets work: `POST /resume`. `200` = back in seconds (on host). `202 {waking, expected_seconds}` = about 1 min from storage. Show "Waking your computer…". Any action also wakes it, but we want explicit control. Re-fetch `GET /computers/{id}` afterwards: `instance_id` and the password may change |
| **Per-bot screen** | `POST /computers/{id}/screens {}` → `screen-10N`. Persist `bot_id → screen_id`. Check with `GET …/screens/{screenId}` (404 = gone). Screens survive restart and start (recreated empty) and should survive suspend/resume with windows intact **[inferred]**. Max 4 per box |
| **More than 4 bots** | Screen **pool/lease** **[inferred design]**. Bots get a screen only while they actively use a computer. Idle bots release theirs: optionally snapshot the browser state (profile dirs on disk persist), then destroy the screen. Queue or LRU-evict when all 4 are busy. Also consider a bigger box. Reserve `default` for the human "home" screen, or give it to the Main Bot |
| **Agent loop location** | **Recommended: hybrid.** The *reasoning* (Grok calls, transcript, approvals, memory, routines) runs **server-side** in our workers, as in SPEC §Decisions 2. *Execution* goes through a **box host** daemon inside the VM. Reasons below |
| **Box host ↔ server link** | Supervisord service from the template. It **dials out** to `wss://9am9am/api/box/connect` (no inbound to the VM exists) and reconnects with backoff. Auth options, in order of preference: (1) a per-box bootstrap token we write at provisioning via `POST /computers/{id}/bash` (e.g. to `/etc/9am9am/token`, 0600) or via an Orgo workspace secret and `secrets/sync` → `/root/.orgo-secrets.env`, rotated by us; (2) an Orgo identity token verified by our server with `GET /computers/{desktop_id}` **[inferred]**, only where eligible. **Don't** use the Orgo computer key (`orgo_api_key` template secret): it isn't held to its computer |
| **Computer-use tools** | Box host per bot screen: `scrot`/`import -window root` with `DISPLAY=:10N` for screenshots, `xdotool` for click, move, drag, mouse down/up, key and type, Playwright/CDP for `browser_*` tools, and shells for Shell/AwaitShell. Fallback when the host is down: Orgo REST with `?screen=` |
| **Live viewer (Watching / Take control / I'm done)** | Our WebSocket relay to `…/ws/websockify?screen=<botScreen>&token=<pw>`. It filters RFB input in Watching mode and passes it in Take control. On take control, tell the bot loop to pause (a "user in control" flag). Handle `4008` by re-dialing upstream. Thumbnails: periodic `GET /screenshot?screen=…&response_format=binary&format=webp&scale=0.25&quality=60`, or from the host. Click overlay (`computer_actions` frames) comes from the host's action log |
| **Terminal tab** | `wss://…/ws/terminal?token=…&session=bot-<id>` behind the same relay (it needs the password, so it must be proxied) |
| **File transfer** | User→bot: ≤10 MB via `POST /files/upload` **with `desktopId` + `destPath`**, larger via `POST /files/uploads` → PUT → `/complete`, or a box-host pull from our object store via a signed URL. Bot→user: `POST /files/export {desktopId, path}` → signed URL (1 h), or a box-host push to our store |
| **Secrets for connector tokens** | Keep OAuth tokens in our DB. The box host fetches short-lived access tokens per call over its authenticated link. Use Orgo secrets (`POST /api/secrets` with `workspace_id` + `POST /computers/{id}/secrets/sync`) only for box-level bootstrap or config values. They're readable by every process on the box and need a service restart or file re-read to take effect |
| **Golden image ("9AM9AM Bots box")** | Publish and build on Scale with `POST /templates?auto_build=true`, then poll `/build`. Version every host release, or have the host self-update from our server to avoid rebuilds. Launch without template secrets to keep warm restores |
| **Health / repair** | Periodic `GET /computers/{id}/health` for boxes whose host is silent. `POST /heal`, then `{"action":"restart"}`. Re-create screens after an OS update or backup restore (they aren't recreated) |
| **Fleet / capacity** | Scale plan plus capacity add-ons (e.g. 50 Large = 200 vCPU / 800 GB, the self-serve max). Computers past 50 run hourly up to 999; Enterprise beyond. Watch `GET /account/capacity` and turn on auto top-up |

### Why the hybrid (in-VM executor) loop [inferred]

- **Rate limits:** the per-computer control-call cap is 600/min (10/s). Four bots each doing screenshot + action at ~1 Hz ≈ 8 calls/s per box, already near the cap. Account caps (6,000/min on Scale) would limit us to ~50 concurrently active bots fleet-wide. Local `xdotool`/`scrot` calls aren't Orgo control calls.
- **Latency:** every REST action is a round trip through Orgo's proxy with a 30 s cap. Locally, screenshots and clicks are milliseconds.
- **Capability gaps:** REST has no mouse down/up, hold-key, cursor position, horizontal scroll, or exec on non-default screens. Locally we get all of them, plus Playwright.
- **Server-side reasoning keeps everything central:** transcripts, approvals, memory and model keys. A box can be suspended without losing an agent loop mid-step, if the loop checkpoints server-side.
- **Fallback:** if the host isn't installed or responding, the same tool interface can go over Orgo REST with `?screen=`.

### Gaps and risks

1. **4-screen cap** limits how many bots can have a live screen at once. It needs the pool/lease design and RAM sizing.
2. **No xAI on Orgo's hosted agent**, and no screen targeting there. We build the whole loop.
3. **No inbound to the VM.** Template `ports.public` host mappings aren't returned by Get computer, so they're unusable in production. Everything goes through the box host's outbound connection.
4. **Suspend works only for plan (non-hourly) Linux computers without GPU.** Beyond 50 computers, boxes run hourly, which means stop/start = cold boot, lost memory and empty screens. Plan pools cost the same asleep or awake, so hibernation saves little on the plan.
5. **The custom template may be blocked past the 50-computer baseline** ("a template that is not a starter" can't be created past the baseline, L2552). We may need Orgo to grant a higher baseline (contract), or fall back to a curated/blank create plus a bootstrap script.
6. **999-computer hard ceiling** per account (9999 by contract).
7. **The VNC password is root.** Never send it to browsers; relay and filter. Embeds idle-close after 30 min without human input.
8. **Identity tokens are conditional and rolling out.** Computer keys are over-privileged. Prefer our own bootstrap token.
9. **`auto_stop_minutes` idle semantics are undocumented,** and template launches ignore the template value.
10. **Secrets are visible to every process on the box.** A user's bots share one VM, so there's no isolation between bots. Fine within one user, but bots can read each other's files and tokens.
11. **Plain `POST /files/upload` pushes to every running computer in the workspace.** Use `destPath` or one workspace per user.
12. **State drift:** `instance_id`, host, ports and VNC password change on start-from-archive, wake-from-storage and RAM resize. Always key on the UUID and re-fetch.
13. **Operations can enter `needs_review` for up to ~2 h** and block start/stop (`409 OPERATION_NEEDS_REVIEW`). The UX needs a "repairing" state.
14. **Data-center IPs trigger bot checks.** The BYO proxy covers Chrome-policy browser traffic only.
15. **Many endpoints we'd like** (auto-stop, backups, usage/obs, egress IPs, WebRTC) exist only in the permission tables, with no documented shapes. Validate before depending on them.

---

## 11. Quick reference: every endpoint

Paths are relative to `https://www.orgo.ai/api` unless they start with `wss://` or `http://169.254…`. **OAS** = in the OpenAPI 2.0.0 spec. **doc** = documented reference or guide page but not in the spec. **perm** = permission table only.

### Workspaces and account

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET | `/workspaces` | List workspaces with `desktops`. Creates a default workspace if there are none | OAS |
| POST | `/workspaces` | Create `{name, icon_url?, status?}`. Account key | OAS |
| GET | `/workspaces/{id}` | Get, with `desktops` | OAS |
| PATCH | `/workspaces/{id}` | `{name, icon_url, status}` | OAS |
| POST | `/workspaces/{id}/delete` (also `DELETE /workspaces/{id}`) | Delete the workspace and all its computers | OAS |
| GET | `/account/capacity` | Limits, pools, `computers.{total,baseline,ceiling,can_raise}`, rates | OAS |
| POST | `/account/capacity` | Buy room: size or `addon {vcpu, ram_gb, disk_gb}`, `term`, `dry_run`. Idempotency-Key | OAS |
| PATCH | `/account/capacity` | Reduce legacy capacity | OAS |
| GET | `/billing/quota` | Computers, baseline, ceiling, can_raise | doc |
| POST | `/checkout` | `{plan: hacker_v2\|startup_v2\|scale_v2}` → Stripe URL (**changes the plan if one exists**) | doc |
| POST | `/credits/checkout` | `{amount_cents}` → Stripe URL | doc |
| POST | `/v1/support/ask` | `{question \| messages}` → `answer`, `links` | OAS |

### API keys

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/keys` | `{name, permissions, expires_in_days \| expires_at, workspace_id \| computer_id}`. **Always send a body** | doc |
| GET | `/keys[?workspace_id=&computer_id=]` | List | doc |
| GET / PATCH / DELETE | `/keys/{id}` | Read / change (name, permissions, expiry, workspace) / revoke | doc |
| GET / DELETE | `/computers/{id}/keys[/{keyId}]` | Keys bound to a computer | doc |
| POST | `/cli/auth/start`, `/cli/auth/poll` | Device-approval key flow | doc |

### Computers: CRUD and lifecycle

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/computers` | Create: `workspace_id, name, os, cpu, ram, disk_size_gb, resolution, gpu, template_ref, billing_mode, ends_in_hours`. Idempotency-Key | OAS |
| GET | `/computers/{id}` | Status, connection, `vnc_password`, `asleep`, `suspension`, ends | OAS |
| PATCH | `/computers/{id}` | `name, bot_mascot, ends_at \| ends_in_hours` | OAS |
| DELETE | `/computers/{id}` | Delete permanently | OAS |
| POST | `/computers/{id}/start` | `?async`, `{ends_in_hours \| ends_at, async}` | OAS |
| POST | `/computers/{id}/stop` | Archive the disk → `frozen`. `?async` | OAS |
| POST | `/computers/{id}/restart` | Reboot in place. `{clear_disk}` (GPU only) | OAS |
| POST | `/computers/{id}/suspend` | Sleep with memory (Linux, plan, no GPU) | OAS |
| POST | `/computers/{id}/resume` | Wake. `{ends_in_hours \| ends_at}` | OAS |
| GET | `/computers/{id}/suspension` | Suspend/resume progress. `?after=resume` | OAS |
| GET | `/computers/{id}/operations/{operationId}` | Poll an async start, stop or restore | OAS |
| POST | `/computers/{id}/extend` | `{hours 1-2160}`. Idempotency-Key required | OAS |
| POST | `/computers/{id}/clone` | `{name?, workspace_id?}` (disk copy) | OAS |
| POST | `/computers/{id}/fork` | `{workspace_id?}` (live copy with memory) | OAS |
| PATCH | `/computers/{id}/move` | `{workspace_id}` | OAS |
| PATCH | `/computers/{id}/resize` | `{vcpus, mem_gb, disk_size_gb, bandwidth_limit_mbps, auto_stop_minutes}` | OAS |
| GET | `/computers/{id}/resize` | Resize ceilings | OAS |
| GET | `/computers/{id}/health` | Non-mutating checks | doc |
| POST | `/computers/{id}/heal` | `{action: auto\|restart}` | doc |
| GET | `/computers/{id}/vnc-password` | `{password, desktop_api_token}` (UUID only) | OAS |
| GET / POST / DELETE | `/computers/{id}/ssh` | Direct SSH settings / `{public_key}` / off | OAS |
| GET / POST | `/computers/{id}/egress/upstream` | BYO browser proxy `{mode, upstream}` | doc |
| GET / PATCH | `/computers/{id}/auto-stop` | Idle-sleep minutes | perm |
| GET / POST | `/computers/{id}/backups`, `/computers/{id}/backup/restore` | Daily backups / restore (new or in place) | perm |
| POST | `/computers/{id}/ensure-running` | Status without starting | perm |
| GET | `/computers/{id}/runtime`, `/computers/{id}/snapshots` | Suspend/resume history; snapshots | perm |
| POST | `/computers/{id}/webrtc` | Toggle UDP video | perm |
| POST | `/computers/usage`; GET `/obs/{id}` | CPU, memory, disk for up to 100 computers / live | perm |
| POST | `/computers/{id}/billing` | Move an hourly computer onto the plan | perm |
| GET / POST / PATCH / DELETE | `/egress-ips[/{id}]`, `/egress-devices[/{id}]` | Dedicated IPs / paired egress devices | perm |
| GET / POST | `/projects/by-name/{name}/computers` | List / create a computer by workspace name | perm |

### Computer actions

All accept `?screen=` except wait and exec.

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET | `/computers/{id}/screenshot` | `screen, response_format, format, quality, scale, image_encoding` | OAS |
| POST | `/computers/{id}/click` | `x, y, button, double, modifiers[], repeat` | OAS |
| POST | `/computers/{id}/mouse-move` | `x, y` | OAS |
| POST | `/computers/{id}/drag` | `start_x, start_y, end_x, end_y` | OAS |
| POST | `/computers/{id}/type` | `text, delay_ms` (keep under ~2,500 chars) | OAS |
| POST | `/computers/{id}/key` | `key` (X11 keysym, `+` combos) | OAS |
| POST | `/computers/{id}/scroll` | `x, y, direction, amount` | OAS |
| POST | `/computers/{id}/wait` | `seconds` ≤60 | OAS |
| POST | `/computers/{id}/bash` | `command, timeout` (200 default, 300 max) | OAS |
| POST | `/computers/{id}/exec` | `code, timeout` (10 default, 300 max). Default display only | OAS |
| GET / HEAD / POST / PUT / PATCH / DELETE | `/desktops/{id}/proxy/{path}` | Orgo Desktop API passthrough (e.g. `/health`). Bearer: API key, `desktop_api_token` or identity token | doc |

### Screens

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET | `/computers/{id}/screens` | List `{id, display, width, height, vnc_port, ws_port, default}` | OAS |
| POST | `/computers/{id}/screens` | Create `{width?, height?}` (both or neither). Max 4 per VM | OAS |
| GET | `/computers/{id}/screens/{screenId}` | Get (404 if missing) | OAS |
| PATCH | `/computers/{id}/screens/{screenId}` | Resize `{width, height}` (xrandr) | OAS |
| DELETE | `/computers/{id}/screens/{screenId}` | Destroy (`204`; `default` → 409) | OAS |

### WebSockets

All take `?token=<vnc_password>`.

| Path | Purpose / key params | Src |
|---|---|---|
| `wss://www.orgo.ai/desktops/{id}/ws/websockify` | VNC (RFB). `?screen=` | doc |
| `wss://www.orgo.ai/desktops/{id}/ws/terminal` | PTY. `cols, rows, session` | doc |
| `wss://www.orgo.ai/desktops/{id}/ws/audio` | PCM s16le. `sample_rate, channels` | doc |
| `wss://www.orgo.ai/desktops/{id}/ws/events` | Desktop events (subscribe) | doc |
| `wss://www.orgo.ai/desktops/{id}/ws/rtc`, `/ws/stats` | WebRTC screen / stream stats | perm |

### Files

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/files/upload` | Multipart `file, workspaceId, desktopId?, destPath?` (≤10 MB) | OAS |
| POST | `/files/uploads` | Start a large upload `{desktopId, filename, size_bytes, destPath?}` → `upload_url` + `upload_token` | OAS |
| POST | `/files/uploads/complete` | `{desktopId, upload_token}` | OAS |
| POST | `/files/export` | `{desktopId, path}` → signed `url` (≤2 GB) | OAS |
| GET | `/files?workspaceId=&desktopId=` | List | OAS |
| GET | `/files/download?id=` | Signed URL (1 h) | OAS |
| DELETE | `/files/delete?id=` | Delete the stored file | OAS |

### Secrets

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/secrets` | Save `{name, value, workspace_id?}` | doc |
| GET | `/secrets` | Secret names (Bops computer keys and tokens) | perm |
| POST | `/computers/{id}/secrets/sync` | Apply to one computer `{names, wake, remove, dry_run}` | OAS |
| POST | `/secrets/apply` | Apply to many `{names, computer_ids?, workspace_id?, remove?, dry_run?}` | OAS |

### Templates

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET | `/template-schema` | JSON Schema (public) | OAS |
| GET | `/templates[?namespace=]` | List yours | OAS |
| POST | `/templates?auto_build=&force=` | Publish (YAML or JSON body) | OAS |
| POST | `/templates/validate` | Validate | OAS |
| GET | `/templates/global` | Curated list | OAS |
| GET | `/templates/global/{ns}/{name}` | Curated versions | OAS |
| GET | `/templates/{ns}/{name}` | Your versions | OAS |
| GET / DELETE | `/templates/{ns}/{name}/{version}` | Get / delete a version | OAS |
| POST / GET / DELETE | `/templates/{ns}/{name}/{version}/build` | Build (`?tier, launch, project`) / status / cancel | OAS |
| GET | `/templates/{ns}/{name}/{version}/build/events` | SSE build log (registry-host builds) | OAS |
| POST / DELETE | `/templates/run` | Test-run `{ref}` / stop `?id=` | OAS |

### Orgo hosted agent and threads

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/v1/chat/completions` | `computer_id, messages, model, stream, max_steps, thread_id`. Headers `X-OpenAI-Key`, `X-Anthropic-Key` | OAS |
| GET / POST | `/v1/threads[?computer_id=]` | List / create thread | doc |
| GET / DELETE | `/v1/threads/{thread_id}` | Get / delete thread | doc |
| GET / POST / PATCH / DELETE | `/chat/threads[/{id}]`, `/chat/threads/{id}/title`, `/chat/threads/{id}/run` | Dashboard thread surface | doc |

### Workflows (preview)

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET / POST | `/workflows` | List / create `{name, describe \| lines}` | OAS |
| GET / PUT / PATCH / DELETE | `/workflows/{id}` | Get / replace lines (If-Match) / `{on, name, settings, computer, time_zone}` / delete | OAS |
| POST | `/workflows/{id}/validate` | Dry check | OAS |
| GET / POST | `/workflows/{id}/runs` | List runs / start `{inputs}` | OAS |
| GET | `/workflows/{id}/runs/{runId}` | Get run | OAS |
| POST | `/workflows/{id}/runs/{runId}/stop`, `/answer` | Stop / answer `{choice}` | OAS |
| GET | `/workflows/{id}/events` | SSE | OAS |

### Clients (Invoicing)

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET / POST | `/v1/accounts` | List / add `{name, external_id, max_computers, monthly_ai_cap_usd}` → `api_key` | OAS |
| GET / PATCH / DELETE | `/v1/accounts/{id}` | Get / update / close | OAS |
| POST | `/v1/accounts/{id}/key` | Replace the client key | OAS |
| POST / DELETE | `/v1/accounts/{id}/transfer` | Transfer `{email}` / withdraw | OAS |
| GET / POST / PATCH / DELETE | `/v1/accounts/{id}/billing` | Billing `{email, price_usd, ai_usd}` | OAS |
| POST | `/v1/accounts/{id}/billing/resend` | Resend the payment link | OAS |
| GET | `/v1/accounts/{id}/billing/payments` | Payments | OAS |
| POST | `/v1/accounts/{id}/billing/payments/{payment_id}/refund` | Refund | OAS |

### Agent self-signup

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| POST | `/v1/agents` | Unauthenticated agent signup → `api_key` and $2 credit | OAS |
| GET / POST | `/v1/agents/claim` | Claim status / refresh the claim link | OAS |

### Inside the VM

| Method | Path | Purpose / key params | Src |
|---|---|---|---|
| GET | `http://169.254.169.254/orgo/v1/identity/token[?fresh=1]` | Identity token (header `Metadata-Flavor: Orgo`) | doc |
| GET | `http://169.254.169.254/orgo/v1/identity` | `{desktop_id, vm_id, state}` (no token) | doc |
