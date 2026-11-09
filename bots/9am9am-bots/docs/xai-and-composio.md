# xAI API (Grok 4.7) and Composio: builder's reference for 9AM9AM Bots

Researched 2026-11-09 from the official docs only: `docs.x.ai` (its `llms.txt` index plus per-page `.md` endpoints) and `docs.composio.dev` (its `llms.txt`, `llms-index.txt` and per-page `.md` endpoints), plus `composio.dev/pricing` for plan prices. No accounts were created and no authenticated calls were made, so nothing here has been run against a live key.

Labels: **[D]** = stated in the docs (a URL is given). **[I]** = inferred by us. Verify these before relying on them. **[R]** = our recommendation for 9AM9AM Bots.

> Naming note: docs.x.ai now calls the company "SpaceXSI" in body text (for example "SpaceXSI's frontier model"). The API host is still `api.x.ai`, and the console is `console.x.ai`. [D] https://docs.x.ai/llms.txt

---

## Part A: xAI API for Grok 4.7

### A.0 TL;DR for 9AM9AM Bots

| Question | Answer |
|---|---|
| Model ID | `grok-4.7`. It is the only 4.7 ID on the public API. There is no mini, vision or computer-use variant. "Grok 4.7 Fast" exists only in Cursor and Grok Build. |
| Context / price | 500k tokens. $2 in / $0.50 cached / $6 out per 1M tokens under 200k prompt tokens. $4 / $1 / $12 per 1M at or above 200k. |
| Reasoning | Always on. `reasoning.effort` is `low`, `medium`, `high` (default) or `xhigh`. |
| Images | Input yes (jpg/png, 20 MiB max, no count limit). Output is text only. |
| API | Responses API (`POST https://api.x.ai/v1/responses`), using the `openai` npm package with `baseURL: 'https://api.x.ai/v1'`. Chat Completions still works but is marked legacy. |
| Tools | Function calling (parallel by default) plus server-side `web_search`, `x_search`, `code_interpreter`/`code_execution`, `file_search`/`collections_search`, `image_generation` and remote `mcp`. |
| Computer use | **No native computer-use tool or action format in the docs.** Build it as function tools plus screenshots sent back as `input_image`. See A.5. |
| Voice | `wss://api.x.ai/v1/realtime?model=grok-voice-latest` (alias for `grok-voice-3.0`). It follows OpenAI Realtime conventions. Browsers authenticate with an ephemeral secret from `POST /v1/realtime/client_secrets`. |

### A.1 Models, context, pricing, reasoning, images

**Grok 4.7 at a glance** [D] https://docs.x.ai/developers/grok-4-7.md

| Property | Value |
|---|---|
| Model name | `grok-4.7` |
| Context window | 500,000 tokens |
| Knowledge cutoff | May 2026 |
| Modalities | Text and image input; text output |
| Output limit | "No text output limit". `max_output_tokens` defaults to 128,000 when unset ([responses ref](https://docs.x.ai/developers/rest-api-reference/inference/responses.md)) |
| Reasoning | `low`, `medium`, `high` (default), `xhigh`. It cannot be disabled. |
| APIs | Responses API (the page lists only this one). The legacy Chat Completions page still shows `grok-4.7` examples. |
| Tools | Function calling, web search, X search, code execution |
| Regional | `https://us.api.x.ai/v1` keeps inference in the US at 1.1x token prices. Only `grok-4.7` and `grok-4.6` are served there. |
| Released | September 2026 ([release notes](https://docs.x.ai/developers/release-notes.md)) |

**The 4.7 family.** The models page lists exactly one 4.7 ID, `grok-4.7` [D] https://docs.x.ai/developers/models.md. "Grok 4.7 Fast" is "the same model served on faster infrastructure". It costs 2x the token price, is "available only in Cursor and Grok Build", and is "not available on the public xAI API" [D]. The docs list no `-mini`, `-vision`, `-reasoning` or computer-use variant. Aliases follow the usual pattern: `<model>`, `<model>-latest` and `<model>-<date>` [D]. To confirm what your key can use, call `GET /v1/models` or `GET /v1/language-models`. The second also returns modalities and aliases [D] https://docs.x.ai/developers/rest-api-reference/inference/models.md.

**Other current text models (for reference)** [D] https://docs.x.ai/developers/pricing.md

| Model | Context | In / cached / out per 1M (<200k prompt) |
|---|---|---|
| grok-4.7 | 500k | $2.00 / $0.50 / $6.00 |
| grok-4.6 | 500k | $2.00 / $0.50 / $6.00 |
| grok-4.5 | 500k | $2.00 / $0.30 / $6.00 |
| grok-4.3 | 1M | $1.25 / $0.20 / $2.50 |
| grok-4.20-0309-reasoning / -non-reasoning | 1M | $1.25 / $0.20 / $2.50 |
| grok-4.20-multi-agent-0309 | 1M | $1.25 / $0.20 / $2.50 |
| grok-build-0.1 | 256k | $1.00 / $0.20 / $2.00 |

Long-context pricing: once a request's prompt reaches 200k tokens, every token in that request is billed at the higher rate [D]. Batch API is 20% off for grok-4.7 [D]. Priority processing (`service_tier: "priority"`) costs 2x [D].

**Server-side tool fees** [D] https://docs.x.ai/developers/pricing.md: `web_search` $5 per 1k calls. `x_search` $5 per 1k posts and $10 per 1k profiles (billed per item fetched). `code_execution` $5 per 1k. `attachment_search` $5 per 1k. `collections_search` $2.50 per 1k. Remote MCP is token-cost only. A request that violates the usage guidelines and is caught before generation is charged $0.05.

**Reasoning** [D] https://docs.x.ai/developers/model-capabilities/text/reasoning.md
- Set it with `reasoning: { effort: 'low' | 'medium' | 'high' | 'xhigh' }` on the Responses API. In the AI SDK it is `providerOptions.xai.reasoningEffort`.
- `low` is described as for "Latency-sensitive agentic use and simple tool calling". `xhigh` gives the most depth.
- `presencePenalty`, `frequencyPenalty` and `stop` are rejected on reasoning models.
- **On the Responses API, `grok-4.7` always returns `reasoning.encrypted_content`**, even when `include` does not ask for it. Pass reasoning items back unchanged in the next `input`. This is what keeps the reasoning across turns and keeps the prompt cache warm.
- Reasoning summaries stream as `response.reasoning_summary_text.delta` (or `response.reasoning_text.delta`).
- Sampling caps: temperature is capped at 1 and top_p at 0.95 ([responses ref](https://docs.x.ai/developers/rest-api-reference/inference/responses.md)). `logprobs` is silently ignored on grok-4.20 and newer.

**Image input** [D] https://docs.x.ai/developers/model-capabilities/images/understanding.md
- Responses format: `{ type: 'input_image', image_url: 'data:image/png;base64,...' | 'https://...', detail: 'auto' | 'low' | 'high' }` next to `{ type: 'input_text', text }`.
- Limits: 20 MiB per image, no limit on image count, jpg/jpeg/png only, any order of text and images.
- Warning from the docs: "When sending images, it is advised to not store request/response history on the server. Otherwise the request may fail." This matters a lot for the computer-use loop (A.5).

### A.2 API surfaces: base URL, auth, Responses vs Chat Completions, SDKs

- **Base URL:** `https://api.x.ai/v1`, or `https://us.api.x.ai/v1` for US-only processing. **Auth header:** `Authorization: Bearer $XAI_API_KEY` [D] https://docs.x.ai/developers/quickstart.md
- **Responses API** (`POST /v1/responses`) is "the recommended way". It offers stateful `previous_response_id`, 30-day storage, encrypted reasoning, native server-side tools, and "all new capabilities delivered here first" [D] https://docs.x.ai/developers/model-capabilities/text/comparison.md
- **Chat Completions** (`POST /v1/chat/completions`) is labeled "Deprecated" / "Legacy". It is stateless, returns no reasoning content, and supports function calling only [D].
- **OpenAI SDK compatibility:** official examples use `npm install openai` with `baseURL: 'https://api.x.ai/v1'`. Also: `client.responses.retrieve(id)`, `client.responses.delete(id)` and `client.responses.compact(...)` [D] https://docs.x.ai/developers/model-capabilities/text/generate-text.md
- **Vercel AI SDK:** `npm install ai @ai-sdk/xai zod`, then `xai.responses('grok-4.7')` [D].
- **xAI SDK:** `pip install xai-sdk` is **Python only**, over gRPC. It covers everything, including Collections, Voice and key management [D]. The docs show **no official xAI TypeScript SDK**. `npm install -g @xai-official/grok` is the Grok Build CLI, not an API client [D].
- Set a long client timeout for reasoning models. The docs use `timeout: 360000` [D].
- `safety_identifier`: a stable, hashed end-user ID so xAI attributes policy violations to that user rather than to your key [D] https://docs.x.ai/developers/release-notes.md. **[R]** Send the hashed 9AM9AM Bots user ID.

**Responses API example (TypeScript, Node 22)**

```ts
// npm i openai
import OpenAI from 'openai';

export const xai = new OpenAI({
  apiKey: process.env.XAI_API_KEY!,
  baseURL: 'https://api.x.ai/v1',
  timeout: 360_000,
});

const res = await xai.responses.create({
  model: 'grok-4.7',
  input: [
    { role: 'system', content: 'You are Ada, a 9AM9AM Bots teammate.' },
    { role: 'user', content: 'Summarize my day in 3 bullets.' },
  ],
  reasoning: { effort: 'medium' },
  // Not in the OpenAI TS types yet in all versions; cast if needed. [I]
  prompt_cache_key: 'conv_7f3c…',      // route turns to the same cache server
  safety_identifier: 'u_sha256…',
  store: true,                         // default true; responses kept 30 days
} as any);

console.log(res.id, res.output_text);
// res.output: [{type:'reasoning', encrypted_content, summary:[…]}, {type:'message', content:[{type:'output_text', text}]}]
// res.usage: { input_tokens, output_tokens, input_tokens_details:{cached_tokens}, output_tokens_details:{reasoning_tokens}, … }
```

Response shape [D] https://docs.x.ai/developers/model-capabilities/text/comparison.md:

```json
{ "id": "resp_123", "output": [ { "type": "message", "role": "assistant",
  "content": [ { "type": "output_text", "text": "Hello! How can I help you?" } ] } ] }
```

**Chat Completions example (legacy)**

```ts
const cc = await xai.chat.completions.create(
  { model: 'grok-4.7', messages: [{ role: 'user', content: 'Hello' }] },
  { headers: { 'x-grok-conv-id': 'conv_7f3c…' } },   // cache routing header for Chat Completions
);
console.log(cc.choices[0].message.content);
```

**Streaming.** This uses SSE with `stream: true`. Event names follow OpenAI Responses: `response.output_text.delta`, `response.output_item.added/done`, `response.completed`, `response.incomplete` [D] https://docs.x.ai/developers/model-capabilities/text/streaming.md, https://docs.x.ai/developers/model-capabilities/text/best-practices.md

**Other parameters worth knowing** [D] https://docs.x.ai/developers/rest-api-reference/inference/responses.md
- `instructions` **cannot be combined with `previous_response_id`**: the earlier system prompt is used instead. 9AM9AM Bots should put the system prompt in `input` when it manages history.
- `max_turns` caps server-side agentic tool turns per request. `max_tool_calls` caps tool calls.
- `background` is unsupported. `metadata` and `truncation` are accepted but unsupported.
- `guidelines` sets per-category moderation (nsfw, violence, profanity) [D] (October release notes).
- **WebSocket mode** sends `response.create` messages over `wss://api.x.ai/v1/responses`. It has the same body and event stream, keeps per-connection state, and works with `store:false` and ZDR. It reports up to ~20% lower latency for long tool loops. A connection lasts at most 59 minutes and runs one turn at a time [D] https://docs.x.ai/developers/advanced-api-usage/websocket-mode.md

### A.3 Function / tool calling

[D] https://docs.x.ai/developers/tools/function-calling.md

- **Schema** (Responses format): `{ type: 'function', name, description?, parameters }`. The root of `parameters` must be `type: 'object'`, or an `anyOf`/`oneOf` whose branches are all objects. Anything else returns a 400 error.
- **Strict always:** "the `strict` flag is implicitly always `true`". Tool arguments always match the schema. `additionalProperties` defaults to `false`. A practical subset of JSON Schema is supported: Draft 2020-12 is preferred, and `$ref`/`$defs` work if they are not circular [D] https://docs.x.ai/developers/model-capabilities/text/structured-outputs.md
- **Tool count:** the function-calling page says "max 350 tools per request", but the API reference says "max of 1000 tools". **[I]** Plan for 350.
- **Parallel calls:** on by default. Turn off with `parallel_tool_calls: false`. Process all calls before you continue.
- **tool_choice:** `"auto"` (default), `"required"`, `"none"`, or the docs' object form `{"type":"function","function":{"name":"..."}}`. **[I]** The OpenAI Responses flat form `{type:'function', name}` may also be accepted. Test both.
- **Streaming tool calls:** "With streaming, the function call is returned in whole in a single chunk, not streamed across chunks." Do not build a partial-arguments accumulator. Watch `response.output_item.done` where `item.type === 'function_call'`.
- **Returning results:** send `{ type: 'function_call_output', call_id, output: string }`, either with `previous_response_id` or appended after the full prior `response.output`. On the voice API, `function_call_output` items are not billed [D].
- **Keep reasoning in loops:** "Append the whole array, then your function_call_output items". Do not filter or reorder output items or convert them to Chat Completions messages [D] https://docs.x.ai/developers/model-capabilities/text/best-practices.md

**Agent loop (TypeScript), with history managed by us** (ZDR-friendly and image-safe)

```ts
import type OpenAI from 'openai';

type ToolImpl = (args: any) => Promise<unknown>;

export async function runTurn(opts: {
  xai: OpenAI; tools: any[]; impl: Record<string, ToolImpl>;
  history: any[]; convId: string; userHash: string;
}) {
  const { xai, tools, impl, history, convId, userHash } = opts;
  for (let step = 0; step < 40; step++) {
    const stream = await xai.responses.create({
      model: 'grok-4.7',
      input: history,
      tools,
      reasoning: { effort: 'medium' },
      store: false,                       // we own history (needed for screenshots, see A.5)
      include: ['reasoning.encrypted_content'],
      prompt_cache_key: convId,
      safety_identifier: userHash,
      stream: true,
    } as any);

    let final: any;
    for await (const ev of stream as any) {
      if (ev.type === 'response.output_text.delta') process.stdout.write(ev.delta); // → UI
      if (ev.type === 'response.completed' || ev.type === 'response.incomplete') final = ev.response;
    }

    history.push(...final.output);        // reasoning + messages + function_calls, unchanged
    const calls = final.output.filter((o: any) => o.type === 'function_call');
    if (!calls.length) return final.output_text as string;

    const results = await Promise.all(calls.map(async (c: any) => {
      const fn = impl[c.name];
      const out = fn ? await fn(JSON.parse(c.arguments)) : { error: `unknown tool ${c.name}` };
      return { type: 'function_call_output', call_id: c.call_id, output: JSON.stringify(out) };
    }));
    history.push(...results);
  }
  throw new Error('step budget exhausted');
}
```

### A.4 Built-in server-side tools

[D] https://docs.x.ai/developers/tools/overview.md, https://docs.x.ai/developers/tools/advanced-usage.md

| Responses tool `type` | What it does | Notable options |
|---|---|---|
| `web_search` | Search the web and browse pages | `allowed_domains` / `excluded_domains` (max 5 each), `enable_image_understanding`, `enable_image_search` |
| `x_search` | Search X posts, users and threads | `allowed_x_handles` / `excluded_x_handles` (max 20, mutually exclusive), `from_date`, `to_date`, `enable_image_understanding`, `enable_video_understanding` |
| `code_interpreter` (alias `code_execution`) | Python sandbox | outputs via `include: ['code_interpreter_call.outputs']` |
| `file_search` (alias `collections_search`) | RAG over your Collections | `include: ['file_search_call.results']` |
| `image_generation` | Generate or edit images mid-conversation | billed at Imagine rates |
| `mcp` | Remote MCP server, called by xAI's servers | `server_url` (Streamable HTTP or SSE), `server_label`, `server_description`, `allowed_tools`, `authorization`, `headers`. **`require_approval` and `connector_id` are not supported.** |

How mixing works: server-side tools "execute automatically on SpaceXSI servers". When the model calls a client-side function, "execution pauses" and the call comes back to you. Each client-side round trip starts a fresh `max_turns` budget [D]. Server-side items appear in `output` as `web_search_call`, `x_search_call`, `code_interpreter_call`, `file_search_call` and `mcp_call` [D].

```ts
const res = await xai.responses.create({
  model: 'grok-4.7',
  input: 'What changed in our competitor’s pricing this week? Save a summary.',
  tools: [
    { type: 'web_search', allowed_domains: ['competitor.com'] },
    { type: 'x_search', from_date: '2026-11-02' },
    { type: 'function', name: 'save_note', description: 'Save a note to the bot memory',
      parameters: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } },
  ],
  max_turns: 6,
} as any);
```

Citations come back automatically: as `source` parts in the AI SDK, and in annotations or `citations` otherwise [D] https://docs.x.ai/developers/tools/citations.md

### A.5 Computer use

**Finding:** the xAI API docs have **no native computer-use tool, action schema or computer-use model**. Searching the full `llms-full.txt` turns up "computer use" only in the Grok Bot product docs. The tools list in A.4 is complete. Grok Bot itself is "a computer-use agent" running on a hosted computer [D] https://docs.x.ai/grok-bot/how-grok-bot-works.md. The public API has no dedicated computer-use tool type.

**Recommended pattern [R][I]:** use function tools for actions and send screenshots back as image input.

1. Define small, typed function tools that map one-to-one to Orgo primitives: `computer_screenshot`, `computer_click{x,y,button,double}`, `computer_type{text}`, `computer_key{keys}`, `computer_scroll{x,y,dx,dy}`, `computer_drag`, `computer_wait{ms}`, plus `shell{cmd}`. Give each action a `screenshot_after` default so the model sees the result.
2. `function_call_output.output` is a **string** in every documented example. To show the screen, return a short text result (`"clicked (512,300); screenshot attached"`). Then append a user message with an `input_image` data URL **after** all of that turn's `function_call_output` items. **[I]** The docs do not show images inside `function_call_output`, so test whether array output is accepted.
3. Use `store: false` and keep history yourself, or use WebSocket mode with `store:false`. The docs warn that image requests with server-side storage "may fail". Use `include: ['reasoning.encrypted_content']` and a stable `prompt_cache_key`.
4. Control cost: a 500k context fills quickly with screenshots. Keep the last N (for example 3) screenshots inline and replace older ones with a text caption. Expect each replacement to break the cache prefix from that point, so do it in batches every K steps rather than every step. Alternatively call `client.responses.compact()` every N turns [D] https://docs.x.ai/developers/advanced-api-usage/context-compaction.md.
5. Screenshot hygiene: send PNG/JPEG under 20 MiB. Downscale to a fixed resolution such as 1280x800 and scale coordinates back up. Use `detail: 'high'` only when reading small text.
6. Use `reasoning.effort: 'low'` for routine click loops and `medium`/`high` for planning steps (the docs recommend `low` for "simple tool calling").

```ts
const computerTools = [
  { type: 'function', name: 'computer_screenshot', description: 'Capture the bot’s screen',
    parameters: { type: 'object', properties: {}, additionalProperties: false } },
  { type: 'function', name: 'computer_click', description: 'Click at pixel coords on a 1280x800 screen',
    parameters: { type: 'object', properties: {
      x: { type: 'integer', minimum: 0, maximum: 1279 }, y: { type: 'integer', minimum: 0, maximum: 799 },
      button: { type: 'string', enum: ['left', 'right', 'middle'] }, double: { type: 'boolean' } },
      required: ['x', 'y'] } },
  { type: 'function', name: 'computer_type', description: 'Type text into the focused element',
    parameters: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } },
  { type: 'function', name: 'computer_key', description: 'Press keys, e.g. "ctrl+l" or "Return"',
    parameters: { type: 'object', properties: { keys: { type: 'string' } }, required: ['keys'] } },
];

// after executing the batch of function calls:
history.push(...functionCallOutputs);
history.push({ role: 'user', content: [
  { type: 'input_text', text: 'Screen after your last action:' },
  { type: 'input_image', image_url: `data:image/png;base64,${png}`, detail: 'auto' },
]});
```

### A.6 Stateful conversations, structured outputs, caching, compaction, rate limits

**Stateful** [D] https://docs.x.ai/developers/model-capabilities/text/generate-text.md
- `store` defaults to `true`, and responses are kept for **30 days**. Continue a chain with `previous_response_id`, and use `responses.retrieve` / `responses.delete` to read or remove one.
- Persistent agents that live longer than 30 days must keep their own history plus the encrypted reasoning, and resend it.
- With agentic tools, a follow-up linked by `previous_response_id` "does not need to use the same tools, model parameters" [D] https://docs.x.ai/developers/tools/advanced-usage.md
- **[R]** 9AM9AM Bots bots are persistent, use screenshots, and may need ZDR. So use our own history store (`store:false`) as the source of truth. Treat `previous_response_id` only as an in-process optimization, ideally over WebSocket mode.

**Structured outputs** [D] https://docs.x.ai/developers/model-capabilities/text/structured-outputs.md
- On Responses, use `text: { format: { type: 'json_schema', name, schema, strict: true } }`. Also accepted: `json_object` and `text`. On Chat Completions, use `response_format`.
- These work together with tools, both server-side and client-side ("supported Grok 4 family models").
- What is guaranteed: `format` (date, time, date-time, email, uuid, ipv4, ipv6, uri), string lengths up to 2,048, up to 256 items, up to 64 properties.

```ts
import { z } from 'zod';
import { zodResponseFormat } from 'openai/helpers/zod';
const Plan = z.object({ steps: z.array(z.object({ title: z.string(), needsApproval: z.boolean() })) });
const f = zodResponseFormat(Plan, 'plan');
const r = await xai.responses.create({ model: 'grok-4.7', input: 'Plan the onboarding task',
  text: { format: { type: 'json_schema', name: f.json_schema.name, schema: f.json_schema.schema, strict: true } } } as any);
const plan = Plan.parse(JSON.parse(r.output_text));
```

**Prompt caching** [D] https://docs.x.ai/developers/advanced-api-usage/prompt-caching.md and subpages
- Caching is automatic and matches by prefix from the start of `input`. Cache entries live per server.
- Set `prompt_cache_key` (Responses) or the `x-grok-conv-id` header (Chat Completions) so a conversation keeps hitting the same server. The Grok 4.7 page says "We highly recommend" this.
- Read cache hits from `usage.input_tokens_details.cached_tokens`. Cached tokens still count toward TPM.
- Things that break the cache: editing, reordering or removing earlier items, dropping encrypted reasoning, and a timestamp at the top of the system prompt. Put static content first.

**Context compaction** [D] https://docs.x.ai/developers/advanced-api-usage/context-compaction.md
- `POST /v1/responses/compact` (`client.responses.compact({ model, input })`) returns one opaque compaction item. Spread it into the next request in place of the old history. **[R]** Use it as the 9AM9AM Bots summarizer for long-running bots.

**Rate limits** [D] https://docs.x.ai/developers/rate-limits.md
- Limits are per model, in RPS and TPM. RPS = RPM / 48, with a minimum of 2. Tiers are set by cumulative spend since 2026-01-01: T0 $0, T1 $50, T2 $250, T3 $1k, T4 $5k. Tiers never go down.
- grok-4.7: RPS T0 150 to T4 500. TPM T0 50M to T4 100M.
- grok-voice-3.0: concurrent sessions T0 10, T1 20, T2 50, T3 100, T4 200. TTS: 50 to 500 RPS. STT: 10 to 40 RPS.
- Over the limit returns HTTP `429`. Use exponential backoff.

### A.7 Voice / realtime, ephemeral secrets, TTS/STT, images

**Speech to Speech (realtime)** [D] https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech.md
- Endpoint: `wss://api.x.ai/v1/realtime?model=grok-voice-latest`. `grok-voice-latest` points to `grok-voice-3.0`. `grok-voice-2.0` is deprecated.
- Compatible with the OpenAI Realtime API. The differences are listed at the end of the page: for example, `conversation.item.input_audio_transcription.updated` replaces `.delta`, `rate_limits.updated` is not emitted, and there are xAI extensions `force_message`, `resumption` and `replace`.
- `session.update` fields: `instructions`, `voice` (for example `eve`, or a custom voice ID), `tools` (`function`, `web_search`, `x_search`, `file_search`, `mcp`), `turn_detection: { type: 'server_vad' | null, idle_timeout_ms }`, `reasoning.effort: 'high' | 'none'`, audio `format`/`rate`/`transport` (`audio/pcm` at 8 to 48 kHz, `pcmu`, `pcma`, `opus`), `audio.output.speed` from 0.7 to 1.5, and `resumption.enabled`.
- Function calls: wait for `response.function_call_arguments.done`, then send `conversation.item.create` with `{type:'function_call_output', call_id, output}`, then `response.create`. For parallel calls, send **all** outputs before one `response.create`.
- Session resumption: save `conversation.created.conversation.id` and reconnect with `?conversation_id=`. History expires after 30 minutes idle.
- Pricing: $0.08/min, plus $0.004 per text `conversation.item.create`. `function_call_output` items are free. Sessions with server VAD are billed for their full duration, and push-to-talk sessions only for audio sent and received [D] https://docs.x.ai/developers/pricing.md, https://docs.x.ai/developers/rest-api-reference/inference/voice.md

**Ephemeral client secrets** [D] https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens.md, https://docs.x.ai/developers/rest-api-reference/inference/voice.md

```ts
// server: mint a short-lived secret (never ship XAI_API_KEY to the client)
app.post('/api/voice/secret', async (_req, res) => {
  const r = await fetch('https://api.x.ai/v1/realtime/client_secrets', {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.XAI_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ expires_after: { seconds: 300 } }),   // "session" / "anchor" not supported
  });
  res.json(await r.json());    // → { value: "xai-realtime-client-secret-…", expires_at: 1750000000 }
});

// browser: pass the secret as a WebSocket subprotocol
const { value } = await (await fetch('/api/voice/secret', { method: 'POST' })).json();
const ws = new WebSocket('wss://api.x.ai/v1/realtime?model=grok-voice-latest', [`xai-client-secret.${value}`]);
ws.onopen = () => ws.send(JSON.stringify({ type: 'session.update', session: {
  voice: 'eve', instructions: 'You are Ada…', turn_detection: { type: 'server_vad' },
  tools: [{ type: 'function', name: 'send_task', description: 'Hand work to the bot’s agent loop',
            parameters: { type: 'object', properties: { task: { type: 'string' } }, required: ['task'] } }],
}}));
```

**TTS / STT / custom voices** [D] https://docs.x.ai/developers/model-capabilities/audio/voice.md
- TTS: `POST /v1/tts` `{ text, voice_id, language }` returns audio (MP3 by default, μ-law available), or stream over `wss://api.x.ai/v1/tts`. $15 per 1M characters. List voices with `GET /v1/tts/voices`.
- STT: `POST /v1/stt` multipart `{ model: 'grok-voice-transcribe-2.0', file }` returns `{ text, … }`, or stream over `wss://api.x.ai/v1/stt?model=…&encoding=…&interim_results=true` (Smart Turn, diarization, keyterms). $0.10/hr REST, $0.20/hr streaming.
- Custom voices: `POST /v1/custom-voices` with a reference clip of at most 120 s returns a `voice_id` that works in TTS and realtime.

**Image generation** [D] https://docs.x.ai/developers/model-capabilities/images/generation.md
- `POST /v1/images/generations` (`client.images.generate({ model: 'grok-imagine-image-2.0', prompt, aspect_ratio, quality: 'low'|'medium'|'auto', response_format: 'url'|'b64_json' })`). Costs $0.04 per image.
- `deferred: true` with polling of `GET /v1/images/{request_id}` is also available. Inside a chat, use the `image_generation` tool instead.

### A.8 How 9AM9AM Bots uses xAI

1. **Agent loop:** each bot turn runs the A.3 loop on `grok-4.7` through the `openai` package. 9am9am-bots owns history in SQLite (`store:false`, encrypted reasoning kept as-is). `prompt_cache_key = bot_id:conversation_id`, `safety_identifier = sha256(user_id)`. Run compaction when context passes a budget we set (this project uses the token count of the previous call).
2. **Effort policy:** `low` for computer-use step loops and routine tool calls, `medium` for chat, `high` for planning or "deep work" routines. Expose `xhigh` as an advanced bot setting.
3. **Tools per turn:** our own functions (Orgo computer actions, shell, memory, `send_to_user`, `request_approval`, `send_to_bot`) plus the Composio tools from B.5. Add server-side `web_search` and `x_search` (cheap, citations included). Avoid xAI remote `mcp` for user apps, because xAI cannot apply our approvals (see B.5).
4. **Computer use:** follow the A.5 pattern on the user's Orgo VM, one screen per bot.
5. **Voice:** a browser or mobile client gets a 5-minute secret from our server and connects to `grok-voice-latest`. The voice agent gets a small relay toolset (`send_task`, `recall_messages`, `switch_bot`, `end_call`) that hands work to the text agent loop, the same split Grok Bot uses.
6. **Background routines:** use the Batch API (20% off) only for non-interactive digest jobs. Everything else goes through the normal or WebSocket path.

---

## Part B: Composio

### B.0 TL;DR for 9AM9AM Bots

| Question | Answer |
|---|---|
| Current paradigm | **Sessions** (`composio.create(userId, config)`, alias `composio.sessions.create`). They were formerly the "Tool Router". Direct `tools.get/execute` is legacy. |
| Packages | `@composio/core` (0.28.0 as of 2026-11-03, ESM-only, Node ≥ 22.22.3) and `@composio/openai` (`OpenAIResponsesProvider`). There is no xAI-specific provider. |
| Connect an app | `session.authorize('gmail', { callbackUrl })` or `composio.connectedAccounts.link(userId, authConfigId, { callbackUrl })` returns `redirectUrl` (hosted Connect Link). `initiate()` is retired for Composio-managed OAuth. |
| Marketplace | `composio.toolkits.get({ search, category, sortBy, limit, cursor })` (REST `GET /api/v3.1/toolkits`) returns name, slug, `meta.logo`, `meta.description`, `meta.categories`, `auth_schemes`, `composio_managed_auth_schemes`, tool and trigger counts. |
| App sign-in (IdP)? | **No.** Composio has no end-user login or identity product. Use a real auth layer for 9AM9AM Bots sign-in and pass our user ID to Composio. See B.3. |
| Approvals | Per toolkit, tool or hint: `requireApproval` (experimental). It raises `ComposioToolInputRequiredError`, which we render as a 9AM9AM Bots approval card. |
| Routines | Triggers (`composio.triggers.create(userId, slug, { triggerConfig })`) feed one project webhook. Verify with `composio.triggers.parse(req, { verifySecret })`. |

### B.1 Core concepts and renames

[D] https://docs.composio.dev/reference/glossary.md, https://docs.composio.dev/docs/how-composio-works.md

| Concept | What it is |
|---|---|
| **User ID** (`user_id`) | Your app's stable user identifier. Composio stores connections under it, fully isolated. Use the DB primary key, not an email, and "Never: `default` in production". |
| **Toolkit** | One app (`github`, `gmail`, `slack`). Lowercase slug. |
| **Tool** | One action, `{TOOLKIT}_{ACTION}` (for example `GITHUB_CREATE_ISSUE`), with input and output JSON Schema. Toolkits are versioned. |
| **Auth config** (`ac_…`) | A blueprint for how a toolkit authenticates: scheme (`OAUTH2`, `API_KEY`, `BEARER_TOKEN`, `BASIC`), scopes and credentials. It is created automatically for managed auth. |
| **Connected account** (`ca_…`) | A user's stored credentials for a toolkit. Composio refreshes them automatically. Status is `INITIATED`, `ACTIVE`, `EXPIRED`, `REVOKED`, or disabled. A user can have several per toolkit (multi-account mode, aliases). |
| **Connect Link** | The hosted authorize page at `connect.composio.dev/link/ln_…`. It is the `redirectUrl` of a connection request. |
| **Session** | The per-user runtime context: tool access filters, auth configs, account selection, approvals, sandbox, and an optional MCP endpoint. Sessions "persist on the server and don't expire". Store the `sessionId` and reuse it with `composio.use(id)`. |
| **Meta tools** | Included in every default session: `COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_GET_TOOL_SCHEMAS`, `COMPOSIO_MANAGE_CONNECTIONS`, `COMPOSIO_MULTI_EXECUTE_TOOL`, `COMPOSIO_REMOTE_WORKBENCH`, `COMPOSIO_REMOTE_BASH_TOOL`. They give runtime discovery without loading thousands of schemas. |
| **Trigger type / instance** (`ti_…`) | An event kind, and that kind activated for one connected account. Delivery is realtime (provider push) or polling (managed auth polls no faster than every 2 min). |
| **Webhook subscription** | One outbound URL per project for signed events. |
| **Provider** | An adapter that formats tools for a framework (`@composio/openai`, `@composio/vercel`, …). |
| **Project / Org** | Project API key (`x-api-key`) scopes everything. Org key is `x-org-api-key`. User key is `x-user-api-key`. |

**Renames** [D] (the "Terminology Migration" table repeated on every docs page, plus https://docs.composio.dev/docs/migration-guide.md)
- entity ID → **user ID**; actions → **tools**; apps → **toolkits**; integration → **auth config**; connection → **connected account**; `ComposioToolSet`/`OpenAIToolSet` → `Composio` class plus a provider.
- Experimental **Tool Router** (`composio.experimental.tool_router`) → **Sessions**. The REST path is still `/api/v3.1/tool_router/session`, and `composio.toolRouter` is a legacy alias.
- Per-toolkit **MCP server configs** → session `mcp: true`. `mcp.composio.dev` was removed in March 2026.
- **Workbench** → **Sandbox** (the `workbench` key still works). **Premium Tools** → **Instant Tools**.
- `connectedAccounts.initiate()` → **`link()`** for Composio-managed OAuth. Cutover dates: 2026-05-08 for new orgs, 2026-07-03 for all orgs. After that, `initiate()` throws `ComposioLegacyConnectedAccountsEndpointRetiredError` for that combination. `initiate()` still works for custom OAuth apps and non-OAuth schemes [D] https://docs.composio.dev/reference/sdk-reference/typescript/connected-accounts.md
- REST **v3.1** is current at `https://backend.composio.dev/api/v3.1`, and tool endpoints default to the latest toolkit version. v3 is frozen but supported.

### B.2 Auth flows

**Create an auth config** [D] https://docs.composio.dev/docs/authentication/programmatic-auth-configs.md

```ts
import { Composio } from '@composio/core';
const composio = new Composio(); // reads COMPOSIO_API_KEY

// Composio-managed OAuth app (zero setup; consent screen shows Composio's app)
const gh = await composio.authConfigs.create('github', { type: 'use_composio_managed_auth', name: 'GitHub' });

// Our own OAuth app (our branding/scopes/quota). Register this redirect URI with the provider:
//   https://backend.composio.dev/api/v3/toolkits/auth/callback
const notion = await composio.authConfigs.create('notion', {
  type: 'use_custom_auth', authScheme: 'OAUTH2', name: 'Notion',
  credentials: {
    client_id: process.env.NOTION_CLIENT_ID!, client_secret: process.env.NOTION_CLIENT_SECRET!,
    oauth_redirect_uri: 'https://backend.composio.dev/api/v3/toolkits/auth/callback',
  },
});

// API-key toolkit where the user supplies the key at connect time
const pplx = await composio.authConfigs.create('perplexityai', { type: 'use_custom_auth', authScheme: 'API_KEY', name: 'Perplexity', credentials: {} });

// discover required fields dynamically
await composio.toolkits.getAuthConfigCreationFields('notion', 'OAUTH2', { requiredOnly: true });
```

Some toolkit defaults still use the legacy callback `https://backend.composio.dev/api/v1/auth-apps/add`. Set `oauth_redirect_uri` explicitly so it matches what the provider has registered [D].

**Managed vs custom:** the managed OAuth catalog lists 127 toolkits, including GitHub, Gmail, Google Calendar and Slack. 275 toolkits require your own app, **including Twitter/X** [D] https://docs.composio.dev/toolkits/managed-auth.md. To check one toolkit, read `composio_managed_auth_schemes` on `GET /api/v3.1/toolkits/{slug}`. The pricing page says managed apps work in production but are "not built for production scale. Move to your own app before you scale" [D] https://composio.dev/pricing.

**Initiate a connection** [D] https://docs.composio.dev/docs/authentication/manually-authenticating.md

```ts
const session = await composio.create(userId, { manageConnections: false }); // our UI owns connecting
const req = await session.authorize('gmail', {
  callbackUrl: `https://9am9am.example/connections/callback?attempt=${attemptId}`,
});
await db.connectAttempts.insert({ attemptId, userId, toolkit: 'gmail', connectedAccountId: req.id });
return Response.redirect(req.redirectUrl!);

// Without a session (e.g. a specific auth config):
const req2 = await composio.connectedAccounts.link(userId, 'ac_our_github', { callbackUrl: '…' });
```

- After the provider flow, Composio redirects to `callbackUrl` with `status=success|failed&connected_account_id=ca_…` added [D].
- "Callback query parameters alone aren't proof of ownership." Match the ID against the stored attempt and the signed-in user [D]. An opt-in project setting, **callback identity verification**, lets our server confirm the returning user before the connection activates [D] https://docs.composio.dev/docs/changelog/2026/07/30.md
- In web apps, do not block on `waitForConnection()`. On callback, confirm with `session.toolkits({ toolkits: ['gmail'], isConnected: true })` [D] https://docs.composio.dev/examples/harness-integration.md
- `session.ensureConnected('gmail')` reuses an active connection or starts a flow and waits [D].
- `?locale=es|fr|zh-CN` localizes the Connect Link [D].

**Check status / list / disconnect** [D] https://docs.composio.dev/reference/sdk-reference/typescript/connected-accounts.md

```ts
const { items } = await session.toolkits();            // [{slug,name,logo,isNoAuth,connection:{isActive,connectedAccount:{id,status}}}]
await composio.connectedAccounts.list({ userIds: [userId], toolkitSlugs: ['github'] });
await composio.connectedAccounts.disable('ca_…');      // reversible; enable() to undo
await composio.connectedAccounts.revoke('ca_…');       // best-effort upstream token revocation → REVOKED
await composio.connectedAccounts.delete('ca_…');       // permanent
```

- Lifecycle: OAuth tokens auto-refresh. When a refresh fails, the account moves to `EXPIRED` and the event `composio.connected_account.expired` goes to the webhook subscription. Respond by sending a fresh `session.authorize()` link [D] https://docs.composio.dev/docs/authentication.md
- Composio does not let you export tokens: "connected-account reads redact credentials and aren't a supported token-export workflow". For APIs no tool covers, use `composio.tools.proxyExecute({ endpoint, method, connectedAccountId })` [D].

**White-labeling** [D] https://docs.composio.dev/docs/authentication/white-labeling-authentication.md
- Branded hosted Connect Link: set logo, app title, colors, fonts and per-element styles in Project Settings → White Labeling. There is one theme per project. **The host stays `connect.composio.dev`.** There is no CNAME or self-hosting.
- Own OAuth app: the provider's consent screen shows **our** name and logo. This is set per toolkit through a custom auth config. Existing connections stay on their old config until the user re-authenticates.
- App-owned connection UI: `connectedAccounts.initiate(userId, customAuthConfigId, { callbackUrl, config: { authScheme: 'OAUTH2', val: { status: 'INITIALIZING', long_redirect_url: true } } })` returns the provider URL directly. This works for custom configs only. Composio's callback domain still appears briefly.
- The pricing page lists "Advanced white-labeling" under Enterprise [D].

### B.3 Can Composio be 9AM9AM Bots's own sign-in (IdP)?

**No.** We found no end-user authentication, login or identity-provider product in the Composio docs (full `llms-full.txt` searched). What the docs say instead:
- Consumer-agent guide: "Each person signs in to **your product**, connects their own apps…". Map every signed-in user to a stable ID from your database [D] https://docs.composio.dev/docs/consumer-agents.md
- "derive `user_id` from your authenticated application session, not a browser-supplied field" [D] https://docs.composio.dev/docs/authentication/white-labeling-authentication.md
- "Application user ID … doesn't identify their Gmail or GitHub account". The provider identity has to be fetched separately, and "A GitHub username or display label isn't a verified email address" [D] https://docs.composio.dev/docs/authentication/manually-authenticating.md
- The only "social login" in the docs is the Google/GitHub sign-in for **Composio's own dashboard** (a self-hosting Helm flag). It is not something you can offer your users [D] (llms-full.txt, self-hosted KB entry).

Why a connected account is a poor stand-in for identity [I]:
- You need a `user_id` before you can create a session or Connect Link.
- Composio does not expose OIDC ID tokens.
- Reading the Google email needs a Gmail scope such as `gmail.readonly`. `openid`/`email` alone don't work for `users.getProfile` [D] https://docs.composio.dev/examples/restrict-gmail-domain.md
- With managed apps, the consent screen is Composio's, not ours.

**[R] Recommended pattern**

1. **Sign-in layer:** use a small OAuth/OIDC session library in our Node backend, such as Better Auth or Auth.js, or Google Identity Services plus GitHub OAuth directly. Offer "Sign in with Google/GitHub". Use our own Google/GitHub OAuth clients with minimal scopes (`openid email profile`), and store an HTTP-only session cookie. This produces `users.id` (UUID).
2. **Connectors:** use `composio.create(users.id, …)`. Gmail, Calendar, GitHub and so on are connected through Composio after sign-in, as a separate consent step with real scopes. To get "one-click" onboarding, open the Composio Gmail Connect Link right after first sign-in. To make Google's consent screen show 9AM9AM Bots, register a Google OAuth client as a Composio custom auth config. This can be a different client from the sign-in one, so that sign-in scopes stay minimal.
3. Optional safety check: after connecting, call the provider profile endpoint through `proxyExecute` (or `toolkit.getCurrentUserEndpoint`) and check that the connected mailbox matches the signed-in email, or an allowed domain. Turn on callback identity verification.
4. **Not recommended:** making a Composio Google connection the login itself (provisional user, then `authorize('gmail')`, then proxy `GET /gmail/v1/users/me/profile` and map the email to an account). It works mechanically, but it needs broad Gmail scopes just to log in, shows Composio branding on managed apps, has no ID token or session semantics, and fails whenever the connection expires.

### B.4 Toolkit catalog for a marketplace UI

[D] https://docs.composio.dev/reference/api-reference/toolkits/getToolkits.md, https://docs.composio.dev/reference/sdk-reference/typescript/toolkits.md

- **List and search:** REST `GET /api/v3.1/toolkits` with `x-api-key`. Query params: `search` (name/slug/description), `category`, `sort_by=usage|alphabetically`, `type=native|custom|all`, `include_deprecated`, `limit` (max 1000), `cursor`.
- Each item has: `slug`, `name`, `type`, `auth_schemes[]`, `composio_managed_auth_schemes[]`, `no_auth`, `auth_guide_url`, `instant.supported`, and `meta: { description, logo, app_url, categories[], tools_count, triggers_count, version, created_at, updated_at }`. Pagination fields: `next_cursor`, `total_pages`, `total_items`.
- **Categories:** `GET /api/v3.1/toolkits/categories`, or `composio.toolkits.listCategories()`. They were standardized in February 2026.
- **One / many:** `GET /api/v3.1/toolkits/{slug}` (`composio.toolkits.get('github')`, which includes `authConfigDetails` and `getCurrentUserEndpoint`). `POST /api/v3.1/toolkits/multi` (`composio.toolkits.getMany([...])`).
- **Per-toolkit tools:** `GET /api/v3.1/tools?toolkit_slug=github&query=…&tags=…&limit=…`, or `composio.tools.getRawComposioTools({ toolkits: ['github'], important: false })`. In TypeScript, pass `important: false` (or `limit`, `search`, `tools` or `tags`) to get **all** tools rather than a curated subset [D] https://docs.composio.dev/examples/harness-integration.md
- **Per-toolkit triggers:** `composio.triggers.listTypes({ toolkits: [...] })`, and `getType(slug)` for config and payload schemas.
- **Per-user connection overlay:** `session.toolkits({ isConnected: true })`, which merges the catalog with this user's connection state.
- **Changelog:** `GET /api/v3.1/toolkits/changelog`.

```ts
// Marketplace page (server). The catalog is user-independent → cache it (e.g. 1h).
export async function marketplace(q?: { search?: string; category?: string; cursor?: string }) {
  const toolkits = await composio.toolkits.get({ search: q?.search, category: q?.category,
    sortBy: 'usage', limit: 60, cursor: q?.cursor } as any);   // camelCase SDK params [I: check ToolkitListParams]
  // The docs' harness example iterates the result directly (`for (const toolkit of toolkits)`), so treat it as an
  // array of { slug, name, meta: { logo, description, categories, … }, … } [I: confirm exact field casing in the SDK types]
  return toolkits;
}

export async function connectionsFor(userId: string, sessionId: string) {
  const session = await composio.use(sessionId);
  return (await session.toolkits({ limit: 200 } as any)).items
    .map(t => ({ slug: t.slug, connected: !!t.connection?.isActive, noAuth: t.isNoAuth }));
}
```

The docs' toolkit catalog pages (`https://docs.composio.dev/toolkits/{slug}.md`) list every tool and trigger with its schema. Use them for design-time reference only, not at runtime.

### B.5 Executing tools from the Grok 4.7 agent loop

There is **no xAI or Grok provider**. Because xAI's Responses API takes OpenAI-format function tools, use **`OpenAIResponsesProvider` from `@composio/openai`** with the `openai` client pointed at `api.x.ai` **[I]** (the docs' example uses OpenAI models). Composio's docs say the provider produces `function` tools and turns results into `function_call_output` items keyed by `call_id` [D] https://docs.composio.dev/docs/providers/openai.md.

```ts
// npm i @composio/core @composio/openai openai
import OpenAI from 'openai';
import { Composio, ComposioToolInputRequiredError } from '@composio/core';
import { OpenAIResponsesProvider } from '@composio/openai';

const composio = new Composio({ provider: new OpenAIResponsesProvider() }); // COMPOSIO_API_KEY
const xai = new OpenAI({ apiKey: process.env.XAI_API_KEY!, baseURL: 'https://api.x.ai/v1', timeout: 360_000 });

// One session per (user, bot); store sessionId on the bot row and reuse with composio.use().
const session = await composio.create(user.id, {
  toolkits: bot.enabledToolkits,                       // e.g. ['gmail','googlecalendar','github']
  tags: { requireApproval: ['createHint', 'updateHint', 'destructiveHint'] },  // experimental
  manageConnections: false,                            // our marketplace UI handles connecting
  sandbox: { enable: false },                          // we have our own Orgo computer
});
const composioTools = await session.tools();           // meta tools by default (search/execute)

// Inside the A.3 loop, route Composio calls through the session (NOT tools.execute / userId):
async function runComposioCall(call: { name: string; call_id: string; arguments: string }) {
  try {
    const r = await session.execute(call.name, JSON.parse(call.arguments));
    // r: { data, error, logId, resultType: 'completed' | 'failed' | 'blocked' }
    return { type: 'function_call_output', call_id: call.call_id,
             output: JSON.stringify(r.resultType === 'completed' ? r.data : { error: r.error, logId: r.logId }) };
  } catch (e) {
    if (e instanceof ComposioToolInputRequiredError) {
      // Render e.inputRequests (MCP elicitation forms: approve | always_approve | deny) as a 9AM9AM Bots approval card,
      // persist e.requestState, pause the run. On answer:
      //   await session.execute(call.name, args, { inputResponses: { [id]: { action:'accept', content:{ decision:'approve' } } }, requestState })
      throw new NeedsApproval(call, e.inputRequests, e.requestState);
    }
    throw e;
  }
}
```

Key rules from the docs [D] https://docs.composio.dev/docs/how-composio-works.md, https://docs.composio.dev/docs/governance/approvals.md:
- Run session tools with `session.execute()` or `provider.handleToolCalls(session, response.output)`. **Never** use `composio.tools.execute()` or a user-ID-bound helper: meta tools fail there with "can only be called inside a tool-router session".
- **`handleToolCalls` and the framework providers don't support approvals yet.** When approvals are on, call `session.execute()` yourself, as in the code above.
- Check success with `resultType`, not `error` and not `data.successful`.
- `handleToolCalls(session, …)` needs `@composio/core` ≥ 0.17.0 and `@composio/openai` ≥ 0.12.0. Approvals need `@composio/core` newer than 0.24.0.
- `OpenAIResponsesProvider({ strict: true })` rewrites schemas so optional fields become nullable. **[I]** xAI already enforces strict schemas, so leave `strict` off unless a schema fails to compile on xAI (a root that is not an object returns 400).

**Tool exposure modes** [D] https://docs.composio.dev/docs/configuring-sessions.md
- *Default (meta tools)*: the agent searches and then executes. It costs little context and works for broad "do anything" bots.
- *Preload*: `preload: { tools: ['GMAIL_FETCH_EMAILS', …] }` adds a few known tools (keep it under 20) next to the meta tools.
- *Direct tools preset*: `sessionPreset: SessionPreset.DIRECT_TOOLS` plus `tools: { gmail: { enable: [...] } }` exposes exactly those tools with no meta tools. Use it for narrow role bots.
- Our own planner can index `getRawComposioTools`, or use `composio.tools.getRawToolRouterSessionTools(sessionId)` to get exactly the session's allowed set, then call `session.execute(slug, args)` [D] https://docs.composio.dev/examples/harness-integration.md.

**Per-tool permissions / allow-lists** [D] https://docs.composio.dev/docs/configuring-sessions.md, https://docs.composio.dev/docs/governance/tool-hints.md
- `toolkits: [...]` or `{ enable }` / `{ disable }`. An explicit `{ enable: [] }` denies every app toolkit.
- `tools: { slack: { disable: ['SLACK_DELETE_MESSAGE'] }, gmail: ['GMAIL_SEND_EMAIL', …] }`.
- `tags: { disable: ['destructiveHint'] }`. The hints are `readOnlyHint`, `createHint`, `updateHint` and `destructiveHint`, assigned on a "best-effort" basis. For anything that must be gated, list tools explicitly.
- `requireApproval` works on toolkits, tools or tags. "Always approve" lasts for the rest of the session for that tool and connected account. Approvals can be skipped inside sandbox scripts and disable `proxyExecute` for that toolkit, so keep the sandbox off.
- `connectedAccounts: { gmail: ['ca_work'] }` picks an account. Precedence: connectedAccounts → authConfigs → existing config → new managed config.
- `session.update({...})` patches config (for example when a user enables a new app for a bot). The **Firewall** (before/after-execute rules, paid plans) is available for policy checks such as "never email outside acme.com" [D] https://docs.composio.dev/docs/firewall.md
- Scoped project API keys can be limited to "Session management" or "Session tool execution" [D].

**MCP alternative** [D] https://docs.composio.dev/docs/sessions-via-mcp.md
- `composio.create(userId, { mcp: true })` exposes `session.mcp.url` and `session.mcp.headers` (which carry the `x-api-key`).
- You could pass these to xAI as `{ type: 'mcp', server_url, server_label: 'composio', headers }` [D] https://docs.x.ai/developers/tools/remote-mcp.md. **[R] Don't, for user apps:**
  - The calls run on xAI's servers, so 9AM9AM Bots never sees them before they execute.
  - xAI does not support `require_approval`.
  - Composio approvals over MCP need client elicitation, which xAI's client does not document. Calls are then denied, or allowed without asking under `noElicitationSupportFallback: 'allow'`.
  - We would be handing our Composio project key to a third party in headers.
- MCP is still useful for read-only, direct-preset sessions (for example, research bots) or for external MCP clients.

### B.6 Triggers / webhooks for routines

[D] https://docs.composio.dev/docs/triggers.md, https://docs.composio.dev/docs/setting-up-triggers/creating-triggers.md, https://docs.composio.dev/docs/setting-up-triggers/subscribing-to-events.md

| 9AM9AM Bots routine event | Trigger slug | Kind |
|---|---|---|
| New Gmail message | `GMAIL_NEW_GMAIL_MESSAGE` | poll (≥ 2 min on managed auth; set `polling_interval` on the instance) |
| Slack message in channel | `SLACK_CHANNEL_MESSAGE_RECEIVED` (config `channel_id`, `channel_type`) | webhook |
| Slack DM | `SLACK_DIRECT_MESSAGE_RECEIVED`; bot-token variants `SLACKBOT_RECEIVE_MESSAGE`, `SLACKBOT_CHANNEL_MESSAGE_RECEIVED` | webhook |
| GitHub PR | `GITHUB_PULL_REQUEST_EVENT`, plus `GITHUB_PULL_REQUEST_STATE_CHANGED_TRIGGER`, `GITHUB_PULL_REQUEST_REVIEW_SUBMITTED_TRIGGER`, `GITHUB_PR_REVIEW_COMMENT_CREATED_TRIGGER` | webhook |
| GitHub commit | `GITHUB_COMMIT_EVENT` (config `owner`, `repo`) | webhook |

Slugs come from https://docs.composio.dev/toolkits/gmail.md, https://docs.composio.dev/toolkits/slack.md, https://docs.composio.dev/toolkits/github.md and https://docs.composio.dev/toolkits/slackbot.md. **There is no dedicated Slack "app mention" trigger** in those pages. **[I]** Subscribe to channel messages and filter for `<@BOT_USER_ID>` in our handler.

```ts
// 1) once per project: register the webhook and keep the secret
const sub = await composio.triggers.setWebhookSubscription({ webhookUrl: 'https://9am9am.example/webhooks/composio' });
// store sub.secret as COMPOSIO_WEBHOOK_SECRET   (also: composio.webhooks.subscriptions.rotateSecret())

// 2) per routine: inspect + create a trigger instance for the user's connected account
const t = await composio.triggers.getType('GITHUB_PULL_REQUEST_EVENT'); // t.config / t.payload JSON Schemas
const inst = await composio.triggers.create(user.id, 'GITHUB_PULL_REQUEST_EVENT', {
  connectedAccountId: 'ca_…',                     // optional; else first active account
  triggerConfig: { owner: 'acme', repo: 'web' },
});
await db.routines.update(routineId, { triggerId: inst.triggerId });
// manage: composio.triggers.listActive / enable / disable / update / delete(triggerId)

// 3) webhook handler (Fetch-style; pass the RAW body)
export async function POST(request: Request) {
  const result = await composio.triggers.parse(request, { verifySecret: process.env.COMPOSIO_WEBHOOK_SECRET });
  if (result.rawPayload.type === 'composio.trigger.message') {
    const ev = result.payload;                     // { triggerSlug, payload, … } normalized
    await enqueueRoutineRun({ triggerSlug: ev.triggerSlug, data: ev.payload, raw: result.rawPayload });
  }
  // also arrives here: composio.connected_account.expired, auto-disabled-trigger events
  return Response.json({ ok: true });
}
```

- **V3 envelope** (default for new orgs): `{ id: 'msg_…', type: 'composio.trigger.message', metadata: { log_id, trigger_slug, trigger_id, connected_account_id, auth_config_id, user_id }, data: {…}, timestamp }`.
- **Verification without the SDK:** HMAC-SHA256 over `${webhook-id}.${webhook-timestamp}.${rawBody}` with the secret, base64-encoded, compared against the part of the `webhook-signature` header after the comma. Reject timestamps older than 300 s, which is the SDK default.
- Composio's outbound IPs change, so IP allowlists don't work. Use signatures instead.
- For local development, `composio dev triggers listen --forward http://localhost:8000/webhooks/composio` sends events signed exactly like production. `composio.triggers.subscribe()` (a Pusher WebSocket) is for prototyping only.
- Custom OAuth apps: some providers need Composio's ingress URL registered on your app. See `composio.webhooks.endpoints` and https://docs.composio.dev/docs/setting-up-triggers/custom-oauth-webhooks.md

### B.7 Packages, env vars, limits, pricing

**Packages** [D] https://docs.composio.dev/docs/changelog.md, https://docs.composio.dev/docs/providers.md

| Package | Notes |
|---|---|
| `@composio/core` | 0.28.0 (2026-11-03). ESM-only, **Node ≥ 22.22.3**. Sessions arrived in 0.13.0. Approvals need > 0.24.0. Empty-allowlist fix in ≥ 0.19.1. |
| `@composio/openai` | `OpenAIProvider` (Chat Completions, the SDK default), `OpenAIResponsesProvider`. Needs ≥ 0.12.0 for `handleToolCalls(session, …)`. The docs we read don't state the current version, so check with `npm view @composio/openai version`. |
| `@composio/openai-agents` | For the `@openai/agents` SDK |
| `@composio/vercel` | For the Vercel AI SDK (`ai`). Could pair with `@ai-sdk/xai` **[I]**. |
| `@composio/anthropic` (0.11.7), `@composio/slim` (0.28.0), `@composio/client` | other providers, slim core, raw REST client |
| CLI | `composio` (0.5.x). Commands include `composio login`, `composio dev triggers listen`, `composio triggers info`, `composio generate`. |

**Env vars** [D]: `COMPOSIO_API_KEY` (project key, read automatically), `COMPOSIO_WEBHOOK_SECRET` (our convention, used by the docs and CLI), and optionally `COMPOSIO_USER_API_KEY` / `COMPOSIO_ORG_API_KEY` for org-level or consumer-project use. `COMPOSIO_CUSTOM_HEADERS` is no longer supported; use `defaultHeaders`. REST base is `https://backend.composio.dev/api/v3.1`, with header `x-api-key`.

**Rate limits** [D] https://docs.composio.dev/reference/rate-limits.md: limits are per **organization**, over a fixed 1-minute window shared by all endpoints. Hobby is 2,000 requests/min, Pro 10,000/min, Enterprise custom. Response headers: `X-RateLimit`, `X-RateLimit-Remaining`, `X-RateLimit-Window-Size`, and `Retry-After` on 429. Providers apply their own quotas on top. Managed apps share provider quotas across all Composio customers.

**Pricing** [D] https://composio.dev/pricing (fetched 2026-11-09)

| Plan | Price | Included |
|---|---|---|
| Hobby | $0 | 100k tool calls/mo (≤ 20k on managed apps). 50k triggers/mo (≤ 10k managed). Unlimited connected accounts (first 1,000 free on managed apps). 3 team members. $2 credit. Usage pauses at the cap. |
| Pro | $29/mo | Everything in Hobby, plus $29 monthly credit (no rollover), unlimited members, spend controls and add-ons. Overage billed on the rate card. |
| Enterprise | Custom | Maximum limits, SSO/SCIM, customer-managed keys, advanced white-labeling, MSA/DPA/BAA, SLA |

Sessions are free to create. Triggers are billed per **delivered** event, and polling triggers cost more than realtime ones. Sandboxes are "not billed today" but billing is planned. Instant Tools are billed per call from credit [D].

### B.8 How 9AM9AM Bots uses Composio

1. **Identity:** 9AM9AM Bots runs its own sign-in (Google/GitHub OIDC through Better Auth or Auth.js). `users.id` is the Composio `user_id`. One Composio project per environment.
2. **Marketplace (Apps page):** a server-cached `toolkits.get` list (logo, name, description, categories, managed vs BYO badge) with search and category filters, overlaid with `session.toolkits()` connection state. "Connect" calls `session.authorize(slug, { callbackUrl })` and redirects. The callback verifies the attempt plus `session.toolkits({ isConnected: true })`. Toolkits with `no_auth` are always available. Toolkits like Twitter/X need our own OAuth app.
3. **Branding:** start on managed OAuth. Before launch, register 9AM9AM Bots OAuth apps for Google, Slack and GitHub as custom auth configs, and set Connect Link branding.
4. **Per-bot tool scope:** each bot stores `enabledToolkits` plus optional per-tool allow and deny lists, and gets one long-lived Composio session (`sessionId` on the bot). `session.update()` runs when the user changes the bot's apps. Broad bots use meta tools, and narrow role bots use the direct-tools preset.
5. **Approvals:** set Composio `requireApproval` on `createHint`/`updateHint`/`destructiveHint`, plus explicit tool lists for sends and posts. `ComposioToolInputRequiredError` becomes a 9AM9AM Bots approval card. The answer resumes the call with `inputResponses` and `requestState`. Computer and shell actions use 9AM9AM Bots's own approval policy. Keep the Composio sandbox off.
6. **Agent loop:** Composio tools are passed to `grok-4.7` as plain function tools alongside the Orgo computer tools. Every Composio call runs through `session.execute()`. xAI remote MCP is not used for user apps.
7. **Routines:** the routine editor offers trigger types from `triggers.listTypes({ toolkits })` with a form generated from `getType().config`. Saving runs `triggers.create(user.id, slug, { connectedAccountId, triggerConfig })`. The single webhook verifies the signature, looks up the routine by `metadata.trigger_id`, and starts a bot run with the event as input. `composio.connected_account.expired` produces a "reconnect" card for the user.
8. **Raw API gaps:** use `session.proxyExecute` / `tools.proxyExecute` for endpoints no tool covers (for example the "connected as" profile check).

---

## Open questions / verify with a live key

1. Whether xAI accepts image content inside `function_call_output.output`, which would make screenshot returns simpler than A.5's extra user message.
2. Whether the Responses-style `tool_choice: { type: 'function', name }` is accepted, in addition to the documented nested form.
3. The real per-request tool cap: 350 (guide) or 1000 (API reference).
4. Whether the `OpenAIResponsesProvider` tool output drops straight into xAI unchanged. In particular, check any `strict` field and root schema shapes. Some Composio tools may have schemas whose root is not an object, which xAI rejects with 400.
5. The exact camelCase SDK params for `composio.toolkits.get()` list filters (`sortBy`, `category`, `search`). The REST names are documented; the TypeScript `ToolkitListParams` field names are not shown.
6. The current `@composio/openai` version.
