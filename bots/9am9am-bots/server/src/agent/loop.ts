import OpenAI from 'openai';
import { db, now } from '../db.js';
import { env } from '../env.js';
import { newId, sha256 } from '../lib/ids.js';
import { hub } from '../lib/hub.js';
import { appendHistory, appendRow, emitBot, getBotRow, getRow, loadHistory, setLive, updateRow } from '../store.js';
import type { MessageBody, ToolCallBody } from '../types.js';
import { composioTools, toolkitInfo } from '../composio.js';
import { systemPrompt } from './prompt.js';
import { repairHistory } from './history.js';
import { maybeCompact, noteUsage } from './memory.js';
import { activityFor, functionSchemas, humanizeSlug, policyFor, runComposioTool, toolMap, type ToolCtx, type ToolResult } from './tools.js';

const xai = new OpenAI({ apiKey: env.xaiKey, baseURL: 'https://api.x.ai/v1', timeout: 360_000 });

export type Trigger =
  | { type: 'user'; text: string }
  | { type: 'routine'; routineId: string; name: string; prompt: string }
  | { type: 'bot'; fromBotId: string; fromName: string; text: string; depth: number }
  | { type: 'system'; text: string }
  | { type: 'resume' };

interface PendingState {
  /** function_call items still to process, in order. */
  calls: { call_id: string; name: string; arguments: string; rowSeq: number }[];
  /** outputs already produced for this batch. */
  outputs: any[];
  approvalId: string;
  depth: number;
}

const MAX_STEPS = 60;
const KEEP_IMAGES = 3;

// ---------- per-bot serial queue ----------

const queues = new Map<string, Promise<void>>();
const aborts = new Map<string, AbortController>();

export function enqueue(botId: string, trigger: Trigger) {
  const prev = queues.get(botId) ?? Promise.resolve();
  const next = prev.then(() => runTurn(botId, trigger)).catch((e) => console.error('[agent] turn failed', botId, e));
  queues.set(botId, next);
  next.finally(() => { if (queues.get(botId) === next) queues.delete(botId); });
}

export function interrupt(botId: string) {
  aborts.get(botId)?.abort();
}

/** Run a job in the bot's serial queue, after any running turn (e.g. a manual compaction). */
export function runInBotQueue<T>(botId: string, job: () => Promise<T>): Promise<T> {
  const prev = queues.get(botId) ?? Promise.resolve();
  const result = prev.then(job);
  const next = result.then(() => {}, () => {});
  queues.set(botId, next);
  next.finally(() => { if (queues.get(botId) === next) queues.delete(botId); });
  return result;
}

export const isBotBusy = (botId: string) => queues.has(botId);

function enqueueBotMessage(fromBotId: string, toBotId: string, text: string, depth: number) {
  const from = getBotRow(fromBotId);
  appendRow(toBotId, 'message', { type: 'message', role: 'user', text, from: { kind: 'bot', id: fromBotId, name: from.name } });
  enqueue(toBotId, { type: 'bot', fromBotId, fromName: from.name, text, depth });
}

// ---------- history helpers ----------

/** Keep only the most recent screenshots inline; older ones become a short caption. */
function pruneImages(items: any[]): any[] {
  let seen = 0;
  const out = [...items];
  for (let i = out.length - 1; i >= 0; i--) {
    const it = out[i];
    const content = it?.type === 'function_call_output' ? it.output : it?.role === 'user' ? it.content : null;
    if (!Array.isArray(content)) continue;
    if (!content.some((c: any) => c?.type === 'input_image')) continue;
    seen++;
    if (seen > KEEP_IMAGES) {
      const replaced = content.map((c: any) => (c?.type === 'input_image' ? { type: 'input_text', text: '[older screenshot omitted]' } : c));
      out[i] = it.type === 'function_call_output' ? { ...it, output: replaced } : { ...it, content: replaced };
    }
  }
  return out;
}

function triggerItem(t: Trigger): any | null {
  switch (t.type) {
    case 'user': return { role: 'user', content: t.text };
    case 'routine': return { role: 'user', content: `[Routine "${t.name}" is running now. Do this:]\n${t.prompt}` };
    case 'bot': return { role: 'user', content: `[Message from ${t.fromName}, another Bot on the team. Reply to them with send_to_bot if they asked you something.]\n${t.text}` };
    case 'system': return { role: 'user', content: `[System notice] ${t.text}` };
    case 'resume': return null;
  }
}

// ---------- the turn ----------

async function runTurn(botId: string, trigger: Trigger) {
  const bot = getBotRow(botId);
  if (!bot) return;
  const userId: string = bot.user_id;
  const ctrl = new AbortController();
  aborts.set(botId, ctrl);
  let depth = trigger.type === 'bot' ? trigger.depth : 0;

  const ctx: ToolCtx = { userId, botId, enqueueBotMessage, depth };
  setLive(botId, { isRunning: true, activity: { kind: 'thinking', since: now() }, awaiting: null, startedAt: now() });

  try {
    // A new user message supersedes any pending approval: deny it.
    const pendingRow = db.prepare('SELECT state FROM pending_turns WHERE bot_id=?').get(botId) as any;
    if (pendingRow && trigger.type !== 'resume') {
      const st = JSON.parse(pendingRow.state) as PendingState;
      await finishPending(ctx, st, 'denied-by-new-message');
    }
    if (trigger.type === 'resume') {
      if (!pendingRow) return;
      const st = JSON.parse(pendingRow.state) as PendingState;
      depth = st.depth;
      const paused = await processCalls(ctx, st);
      if (paused) return;
    } else {
      const item = triggerItem(trigger);
      if (item) appendHistory(botId, [item]);
    }

    const tools = [
      ...functionSchemas(),
      ...(await composioTools(userId)),
      { type: 'web_search' },
      { type: 'x_search' },
    ];

    for (let step = 0; step < MAX_STEPS; step++) {
      if (ctrl.signal.aborted) throw new Error('interrupted');
      const result = await callModel(ctx, tools, ctrl.signal);
      appendHistory(botId, result.output);
      const calls = result.output.filter((o: any) => o.type === 'function_call');
      if (!calls.length) break;

      const st: PendingState = {
        calls: calls.map((c: any) => {
          const act = activityFor(c.name);
          const row = appendRow(botId, 'tool-call', { type: 'tool-call', callId: c.call_id, name: c.name, label: act.label, status: 'running', args: safeParse(c.arguments) } satisfies ToolCallBody);
          return { call_id: c.call_id, name: c.name, arguments: c.arguments, rowSeq: row.seq };
        }),
        outputs: [],
        approvalId: '',
        depth,
      };
      const paused = await processCalls(ctx, st);
      if (paused) return;
      if (st.outputs.some((o: any) => o.__endTurn)) break;
    }
  } catch (e: any) {
    const msg = ctrl.signal.aborted ? 'Stopped.' : `Something went wrong: ${e?.message ?? e}`;
    appendRow(botId, 'notice', { type: 'notice', text: msg, tone: ctrl.signal.aborted ? 'info' : 'error' });
    if (!ctrl.signal.aborted) console.error('[agent]', e);
  } finally {
    aborts.delete(botId);
    const stillPending = db.prepare('SELECT 1 FROM pending_turns WHERE bot_id=?').get(botId);
    if (!stillPending) setLive(botId, { isRunning: false, activity: null, awaiting: null, startedAt: null });
    emitBot(botId);
  }
}

/** Run function calls in order. Returns true if the turn paused for approval. */
async function processCalls(ctx: ToolCtx, st: PendingState): Promise<boolean> {
  const { botId, userId } = ctx;
  while (st.calls.length) {
    const c = st.calls[0];
    const args = safeParse(c.arguments) ?? {};
    const row = getRow(botId, c.rowSeq);
    const body = (row?.body ?? {}) as ToolCallBody;

    // Was this call just approved/denied?
    const ap = st.approvalId ? (db.prepare('SELECT * FROM approvals WHERE id=?').get(st.approvalId) as any) : null;
    let decision = policyFor(userId, c.name, args).decision;
    if (ap && ap.call_id === c.call_id) {
      if (ap.status === 'pending') return true;
      decision = ap.status === 'denied' ? 'block' : 'allow';
      st.approvalId = '';
    } else if (decision === 'ask') {
      const pol = policyFor(userId, c.name, args);
      const approvalId = newId('apr');
      const tk = pol.toolkit ? await toolkitInfo(pol.toolkit) : undefined;
      const card = appendRow(botId, 'card', {
        type: 'card', card: 'approval', approvalId, title: `Allow ${getBotRow(botId).name} to ${pol.title.split(': ')[1] ?? pol.title}?`,
        detail: describeArgs(args), tool: pol.key, toolkit: pol.toolkit, toolkitName: tk?.name, logo: tk?.logo, args, status: 'pending',
      });
      db.prepare('INSERT INTO approvals(id,bot_id,row_seq,call_id,tool,status,created_at) VALUES (?,?,?,?,?,?,?)').run(approvalId, botId, card.seq, c.call_id, pol.key, 'pending', now());
      updateRow(botId, c.rowSeq, { ...body, status: 'awaiting-approval' });
      st.approvalId = approvalId;
      db.prepare('INSERT OR REPLACE INTO pending_turns(bot_id,state) VALUES (?,?)').run(botId, JSON.stringify(st));
      setLive(botId, { isRunning: false, activity: { kind: 'waiting_for_you', since: now() }, awaiting: { reason: humanizeSlug(pol.key), since: now() } });
      hub.emit(userId, { type: 'tasks', botId });
      return true;
    }

    let res: ToolResult;
    if (decision === 'block') {
      res = { output: 'The user declined this action. Do not retry it. Find another way, or tell the user what you could not do.', summary: 'Not allowed' };
      updateRow(botId, c.rowSeq, { ...body, status: 'denied', summary: 'Not allowed' });
    } else {
      const act = activityFor(c.name);
      const pol = policyFor(userId, c.name, args);
      const detail = c.name === 'send_to_bot' ? String(args.bot_name ?? '') : !toolMap.has(c.name) && pol.toolkit ? (await toolkitInfo(pol.toolkit))?.name ?? pol.toolkit : undefined;
      setLive(botId, { isRunning: true, activity: { kind: c.name === 'send_to_bot' ? 'messaging_named' : act.kind, detail, since: now() }, awaiting: null });
      try {
        const impl = toolMap.get(c.name);
        res = impl ? await impl.impl(args, { ...ctx, depth: st.depth }) : await runComposioTool(ctx, c.name, args);
        updateRow(botId, c.rowSeq, { ...body, status: 'done', summary: res.summary });
      } catch (e: any) {
        res = { output: `Tool error: ${e?.message ?? e}` };
        updateRow(botId, c.rowSeq, { ...body, status: 'error', summary: String(e?.message ?? e).slice(0, 200) });
      }
    }
    const out: any = { type: 'function_call_output', call_id: c.call_id, output: res.output };
    st.outputs.push(res.endTurn ? { ...out, __endTurn: true } : out);
    st.calls.shift();
  }
  // Batch complete: persist outputs (strip internal markers).
  appendHistory(botId, st.outputs.map(({ __endTurn, ...o }: any) => o));
  db.prepare('DELETE FROM pending_turns WHERE bot_id=?').run(botId);
  setLive(botId, { isRunning: true, activity: { kind: 'thinking', since: now() }, awaiting: null });
  return false;
}

/** When a new message arrives while an approval is pending, close the batch with denials. */
async function finishPending(ctx: ToolCtx, st: PendingState, _reason: string) {
  const { botId } = ctx;
  if (st.approvalId) {
    const ap = db.prepare('SELECT * FROM approvals WHERE id=?').get(st.approvalId) as any;
    if (ap?.status === 'pending') {
      db.prepare("UPDATE approvals SET status='expired', resolved_at=? WHERE id=?").run(now(), ap.id);
      const card = getRow(botId, ap.row_seq);
      if (card && card.body.type === 'card' && card.body.card === 'approval') updateRow(botId, ap.row_seq, { ...card.body, status: 'expired' });
    }
  }
  for (const c of st.calls) {
    st.outputs.push({ type: 'function_call_output', call_id: c.call_id, output: 'Skipped: the user sent a new message before approving this. Do not retry unless they ask.' });
    const row = getRow(botId, c.rowSeq);
    if (row) updateRow(botId, c.rowSeq, { ...(row.body as ToolCallBody), status: 'denied', summary: 'Skipped' });
  }
  appendHistory(botId, st.outputs.map(({ __endTurn, ...o }: any) => o));
  db.prepare('DELETE FROM pending_turns WHERE bot_id=?').run(botId);
}

// ---------- model call with streaming into a message row ----------

async function callModel(ctx: ToolCtx, tools: any[], signal: AbortSignal): Promise<{ output: any[] }> {
  const { botId, userId } = ctx;
  const bot = getBotRow(botId);
  // Runs inside the bot's serial queue, so compaction can't race a turn.
  const history = await maybeCompact(botId, loadHistory(botId)).catch(() => loadHistory(botId));
  // The model is only called when no approval is pending, so every orphaned call can be repaired.
  const input = [{ role: 'system', content: await systemPrompt(userId, botId) }, ...pruneImages(repairHistory(history))];

  const body: any = {
    model: env.xaiModel,
    input,
    tools,
    reasoning: { effort: bot.effort ?? 'medium' },
    store: false,
    include: ['reasoning.encrypted_content'],
    prompt_cache_key: `9am9am:${botId}:${bot.generation}`,
    safety_identifier: sha256(userId).slice(0, 32),
    stream: true,
  };

  let stream: any;
  try {
    stream = await xai.responses.create(body, { signal });
  } catch (e: any) {
    // If a Composio tool schema is rejected, retry with only our own tools.
    if (e?.status === 400 && tools.length > functionSchemas().length + 2) {
      console.warn('[agent] 400 with composio tools, retrying without:', e?.message);
      body.tools = [...functionSchemas(), { type: 'web_search' }, { type: 'x_search' }];
      stream = await xai.responses.create(body, { signal });
    } else throw e;
  }

  let msgSeq: number | null = null;
  let text = '';
  let lastFlush = 0;
  let final: any = null;
  const flush = (streaming: boolean) => {
    const b: MessageBody = { type: 'message', role: 'assistant', text, streaming };
    if (msgSeq === null) msgSeq = appendRow(botId, 'message', b).seq;
    else updateRow(botId, msgSeq, b);
    lastFlush = Date.now();
  };

  for await (const ev of stream as AsyncIterable<any>) {
    switch (ev.type) {
      case 'response.output_text.delta':
        text += ev.delta;
        if (msgSeq === null || Date.now() - lastFlush > 120) flush(true);
        break;
      case 'response.output_item.added':
        if (ev.item?.type === 'web_search_call') setLive(botId, { activity: { kind: 'searching_web', since: now() } });
        if (ev.item?.type === 'x_search_call') setLive(botId, { activity: { kind: 'searching_web', detail: 'X', since: now() } });
        if (ev.item?.type === 'message') setLive(botId, { activity: { kind: 'working', since: now() } });
        break;
      case 'response.output_item.done':
        if (ev.item?.type === 'message' && msgSeq !== null) {
          // finalize this message; a later message in the same response gets its own row
          const citations = (ev.item.content ?? []).flatMap((c: any) => (c.annotations ?? []).filter((a: any) => a.url).map((a: any) => ({ url: a.url, title: a.title })));
          updateRow(botId, msgSeq, { type: 'message', role: 'assistant', text, streaming: false, citations: citations.length ? citations : undefined });
          db.prepare('UPDATE bots SET unread = unread + 1 WHERE id=?').run(botId);
          msgSeq = null;
          text = '';
        }
        break;
      case 'response.completed':
      case 'response.incomplete':
        final = ev.response;
        break;
      case 'response.failed':
      case 'error':
        throw new Error(ev.response?.error?.message ?? ev.message ?? 'model error');
    }
  }
  if (msgSeq !== null) flush(false);
  if (!final) throw new Error('model stream ended without a response');
  if (final.usage?.input_tokens) noteUsage(botId, final.usage.input_tokens, history.length);
  return { output: final.output ?? [] };
}

// ---------- misc ----------

function safeParse(s: string) {
  try { return JSON.parse(s); } catch { return undefined; }
}

function describeArgs(args: any): string {
  if (!args || typeof args !== 'object') return '';
  const inner = Array.isArray(args.tools) ? args.tools.map((t: any) => t.arguments ?? t.args ?? t) : [args];
  const flat = inner.map((a: any) => Object.entries(a ?? {}).filter(([, v]) => typeof v !== 'object' || v === null).slice(0, 6).map(([k, v]) => `${k}: ${String(v).slice(0, 160)}`).join('\n'));
  return flat.join('\n\n').slice(0, 1200);
}

/** Resolve an approval card from the UI. */
export function resolveApproval(userId: string, approvalId: string, decision: 'allow' | 'always' | 'deny') {
  const ap = db.prepare('SELECT a.*, b.user_id FROM approvals a JOIN bots b ON b.id=a.bot_id WHERE a.id=?').get(approvalId) as any;
  if (!ap || ap.user_id !== userId) throw new Error('not found');
  if (ap.status !== 'pending') return;
  const status = decision === 'deny' ? 'denied' : decision === 'always' ? 'always' : 'allowed';
  db.prepare('UPDATE approvals SET status=?, resolved_at=? WHERE id=?').run(status, now(), approvalId);
  if (decision === 'always') db.prepare("INSERT OR REPLACE INTO policies(user_id,tool,mode) VALUES (?,?,'always_allow')").run(userId, ap.tool);
  const card = getRow(ap.bot_id, ap.row_seq);
  if (card && card.body.type === 'card' && card.body.card === 'approval') updateRow(ap.bot_id, ap.row_seq, { ...card.body, status });
  enqueue(ap.bot_id, { type: 'resume' });
}
