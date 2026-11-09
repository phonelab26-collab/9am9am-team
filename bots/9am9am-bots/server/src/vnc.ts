import type { IncomingMessage, Server } from 'node:http';
import { WebSocket, WebSocketServer } from 'ws';
import { db } from './db.js';
import { userFromCookieHeader } from './lib/auth.js';
import { vncAuthResponse } from './lib/des.js';
import { orgo } from './orgo.js';
import { screenFor } from './computer.js';
import { viewerClosed, viewerOpened } from './idle.js';

/**
 * VNC relay: browser <-> our server <-> Orgo websockify.
 * The server performs the RFB handshake upstream with the computer's VNC password
 * (which is root-equivalent and never reaches the browser), then offers the browser
 * an unauthenticated RFB session. In "watch" mode, client input messages
 * (KeyEvent, PointerEvent, ClientCutText) are dropped server-side.
 */

class ByteQueue {
  private buf = Buffer.alloc(0);
  private waiters: { n: number; resolve: (b: Buffer) => void; reject: (e: Error) => void }[] = [];
  private closed: Error | null = null;
  push(b: Buffer) { this.buf = Buffer.concat([this.buf, b]); this.drain(); }
  close(e: Error) { this.closed = e; for (const w of this.waiters) w.reject(e); this.waiters = []; }
  read(n: number): Promise<Buffer> {
    return new Promise((resolve, reject) => {
      if (this.closed) return reject(this.closed);
      this.waiters.push({ n, resolve, reject });
      this.drain();
    });
  }
  takeAll(): Buffer { const b = this.buf; this.buf = Buffer.alloc(0); return b; }
  private drain() {
    while (this.waiters.length && this.buf.length >= this.waiters[0].n) {
      const w = this.waiters.shift()!;
      const out = this.buf.subarray(0, w.n);
      this.buf = this.buf.subarray(w.n);
      w.resolve(Buffer.from(out));
    }
  }
}

const u32 = (n: number) => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };

async function upstreamHandshake(q: ByteQueue, send: (b: Buffer) => void, password: string): Promise<Buffer> {
  await q.read(12); // server ProtocolVersion
  send(Buffer.from('RFB 003.008\n'));
  const n = (await q.read(1))[0];
  if (n === 0) {
    const len = (await q.read(4)).readUInt32BE();
    throw new Error(`VNC refused: ${(await q.read(len)).toString()}`);
  }
  const types = [...(await q.read(n))];
  const choice = types.includes(2) ? 2 : types.includes(1) ? 1 : -1;
  if (choice < 0) throw new Error(`Unsupported VNC security types: ${types.join(',')}`);
  send(Buffer.from([choice]));
  if (choice === 2) {
    const challenge = await q.read(16);
    send(vncAuthResponse(password, challenge));
  }
  const result = (await q.read(4)).readUInt32BE();
  if (result !== 0) {
    let reason = '';
    try { const len = (await q.read(4)).readUInt32BE(); reason = (await q.read(len)).toString(); } catch { /* none */ }
    throw new Error(`VNC auth failed ${reason}`);
  }
  send(Buffer.from([1])); // ClientInit: shared
  const head = await q.read(24); // w,h,pixfmt(16),nameLen
  const nameLen = head.readUInt32BE(20);
  const name = await q.read(nameLen);
  return Buffer.concat([head, name]);
}

async function browserHandshake(q: ByteQueue, send: (b: Buffer) => void, serverInit: Buffer) {
  send(Buffer.from('RFB 003.008\n'));
  await q.read(12);
  send(Buffer.from([1, 1])); // one security type: None
  await q.read(1);
  send(u32(0)); // SecurityResult OK
  await q.read(1); // ClientInit
  send(serverInit);
}

/** Filters a client->server RFB stream, dropping input events. Returns bytes to forward. */
class WatchFilter {
  private pending = Buffer.alloc(0);
  feed(chunk: Buffer): Buffer {
    this.pending = Buffer.concat([this.pending, chunk]);
    const out: Buffer[] = [];
    for (;;) {
      const b = this.pending;
      if (!b.length) break;
      const len = msgLength(b);
      if (len === -1) { this.pending = Buffer.alloc(0); break; } // unknown: fail closed
      if (len === 0 || b.length < len) break; // need more bytes
      const msg = b.subarray(0, len);
      const t = b[0];
      if (t !== 4 && t !== 5 && t !== 6) out.push(Buffer.from(msg));
      this.pending = b.subarray(len);
    }
    return Buffer.concat(out);
  }
}

/** Length of the client message at the head of `b`, 0 if more bytes are needed to know, -1 if unknown. */
function msgLength(b: Buffer): number {
  const t = b[0];
  switch (t) {
    case 0: return 20; // SetPixelFormat
    case 2: return b.length < 4 ? 0 : 4 + 4 * b.readUInt16BE(2); // SetEncodings
    case 3: return 10; // FramebufferUpdateRequest
    case 4: return 8; // KeyEvent
    case 5: return 6; // PointerEvent
    case 6: return b.length < 8 ? 0 : 8 + Math.abs(b.readInt32BE(4)); // ClientCutText (+extended)
    case 150: return 10; // EnableContinuousUpdates
    case 248: return b.length < 9 ? 0 : 9 + b[8]; // ClientFence
    case 251: return b.length < 8 ? 0 : 8 + 16 * b[6]; // SetDesktopSize
    default: return -1;
  }
}

export function attachVncRelay(server: Server) {
  const wss = new WebSocketServer({ noServer: true, handleProtocols: (protocols) => (protocols.has('binary') ? 'binary' : false) });

  server.on('upgrade', (req: IncomingMessage, socket, head) => {
    const url = new URL(req.url ?? '/', 'http://x');
    const m = url.pathname.match(/^\/api\/bots\/([^/]+)\/screen$/);
    if (!m) return; // not ours
    const user = userFromCookieHeader(req.headers.cookie);
    const bot = user ? (db.prepare('SELECT * FROM bots WHERE id=? AND user_id=?').get(m[1], user.id) as any) : null;
    if (!user || !bot) {
      socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
      socket.destroy();
      return;
    }
    const mode = url.searchParams.get('mode') === 'control' ? 'control' : 'watch';
    wss.handleUpgrade(req, socket, head, (client) => relay(client, user.id, bot.id, mode));
  });
}

async function relay(client: WebSocket, userId: string, botId: string, mode: 'watch' | 'control') {
  const fromClient = new ByteQueue();
  const fromUp = new ByteQueue();
  let upstream: WebSocket | null = null;
  let piping = false;
  const filter = new WatchFilter();
  const closeAll = (code = 1000, reason = '') => {
    try { client.close(code, reason.slice(0, 120)); } catch { /* */ }
    try { upstream?.close(); } catch { /* */ }
  };

  client.on('message', (data: Buffer) => {
    if (!piping) return fromClient.push(Buffer.from(data));
    const fwd = mode === 'watch' ? filter.feed(Buffer.from(data)) : Buffer.from(data);
    if (fwd.length && upstream?.readyState === WebSocket.OPEN) upstream.send(fwd);
  });
  client.on('close', () => { viewerClosed(userId); fromClient.close(new Error('client closed')); try { upstream?.close(); } catch { /* */ } });
  viewerOpened(userId); // keeps the computer awake while someone is watching (see idle.ts)

  try {
    const { computerId, screenId } = await screenFor(userId, botId);
    const { password } = await orgo.vncPassword(computerId);
    const upUrl = `wss://www.orgo.ai/desktops/${computerId}/ws/websockify?token=${encodeURIComponent(password)}${screenId !== 'default' ? `&screen=${encodeURIComponent(screenId)}` : ''}`;
    upstream = new WebSocket(upUrl, ['binary']);
    upstream.on('message', (data: Buffer) => {
      if (!piping) return fromUp.push(Buffer.from(data));
      if (client.readyState === WebSocket.OPEN) client.send(data);
    });
    upstream.on('close', (code, reason) => { fromUp.close(new Error(`upstream closed ${code}`)); closeAll(1000, `upstream ${code} ${reason}`); });
    upstream.on('error', (e) => { fromUp.close(e as Error); closeAll(1011, String(e)); });
    await new Promise<void>((res, rej) => { upstream!.once('open', () => res()); upstream!.once('error', rej); });

    const serverInit = await upstreamHandshake(fromUp, (b) => upstream!.send(b), password);
    await browserHandshake(fromClient, (b) => client.send(b), serverInit);
    // Flush anything that arrived during the handshake, then switch to piping.
    piping = true;
    const leftoverUp = fromUp.takeAll();
    if (leftoverUp.length) client.send(leftoverUp);
    const leftoverClient = fromClient.takeAll();
    if (leftoverClient.length) {
      const fwd = mode === 'watch' ? filter.feed(leftoverClient) : leftoverClient;
      if (fwd.length) upstream.send(fwd);
    }
  } catch (e: any) {
    console.warn('[vnc] relay failed', e?.message ?? e);
    closeAll(1011, String(e?.message ?? e));
  }
}
