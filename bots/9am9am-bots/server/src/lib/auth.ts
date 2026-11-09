import type { Context, MiddlewareHandler } from 'hono';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';
import { db, now } from '../db.js';
import { env } from '../env.js';
import { sha256, token } from './ids.js';
import type { User } from '../types.js';

const COOKIE = '9am9am_session';
const TTL_MS = 30 * 24 * 3600 * 1000;

export type AuthVars = { Variables: { user: User } };

/** Never store a usable session token at rest: hash it with a server-side pepper. */
const hashToken = (value: string) => sha256(`${env.sessionSecret}:${value}`);

export function createSession(c: Context, userId: string) {
  const t = token();
  db.prepare('INSERT INTO auth_sessions(token,user_id,created_at,expires_at) VALUES (?,?,?,?)').run(hashToken(t), userId, now(), now() + TTL_MS);
  setCookie(c, COOKIE, t, {
    httpOnly: true,
    sameSite: 'Lax',
    path: '/',
    maxAge: TTL_MS / 1000,
    secure: env.secureCookies,
  });
}

export function destroySession(c: Context) {
  const t = getCookie(c, COOKIE);
  if (t) db.prepare('DELETE FROM auth_sessions WHERE token=?').run(hashToken(t));
  deleteCookie(c, COOKIE, { path: '/', secure: env.secureCookies });
}

export function userFromToken(t: string | undefined): User | null {
  if (!t) return null;
  const r = db
    .prepare('SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?')
    .get(hashToken(t), now()) as any;
  return r ? { id: r.id, email: r.email, name: r.name, avatarUrl: r.avatar_url } : null;
}

export function userFromCookieHeader(cookieHeader: string | undefined): User | null {
  const m = cookieHeader?.split(/;\s*/).find((p) => p.startsWith(`${COOKIE}=`));
  return userFromToken(m ? decodeURIComponent(m.slice(COOKIE.length + 1)) : undefined);
}

export const requireUser: MiddlewareHandler<AuthVars> = async (c, next) => {
  const u = userFromToken(getCookie(c, COOKIE));
  if (!u) return c.json({ error: 'unauthenticated' }, 401);
  c.set('user', u);
  await next();
};
