// Dev-only: `?fixtures` injects sample card/notice/event rows into the open transcript (client-side only)
// so every card state can be checked visually. Buttons hit the real API with fake ids and will show errors.
import type { Row, RowBody } from '@shared/types';
import { useStore } from '../store';

export function injectFixtures() {
  const st = useStore.getState();
  const botId = st.selectedBotId;
  if (!botId) return;
  const gen = st.transcripts[botId]?.generation ?? 0;
  const now = Date.now();
  let seq = 900000;
  const row = (kind: Row['kind'], body: RowBody, ago = 0): Row => ({ botId, generation: gen, seq: ++seq, updatedSeq: seq, kind, body, createdAt: now - ago });
  const rows: Row[] = [
    row('message', { type: 'message', role: 'assistant', text: 'Got it, fixing that line now.' }, 60_000),
    row('message', { type: 'message', role: 'assistant', text: 'Fixed. It now says the computers are VPSs with a full desktop. Want me to send it?' }, 59_000),
    row('card', { type: 'card', card: 'draft', channel: 'message', to: 'Alex Rivera', approvalId: 'fx_draft', status: 'pending', text: 'Hey Alex, quick question. Any plans to support more than 3 seats?\n\nRight now the seat cap is the only thing stopping us.' }, 58_000),
    row('message', { type: 'message', role: 'user', text: 'Awesome, send it.' }, 57_000),
    row('card', { type: 'card', card: 'approval', approvalId: 'fx_appr', title: 'Allow Quinn to send an email?', detail: 'To: alex@example.com\nSubject: Seat cap question', tool: 'GMAIL_SEND_EMAIL', toolkit: 'gmail', args: {}, status: 'pending' }, 56_000),
    row('card', { type: 'card', card: 'approval', approvalId: 'fx_appr2', title: 'Allow Quinn to create a calendar event?', detail: '', tool: 'GOOGLECALENDAR_CREATE_EVENT', toolkit: 'googlecalendar', args: {}, status: 'always' }, 55_000),
    row('card', { type: 'card', card: 'question', question: 'Which posts should go up?', options: ['1. $12/hr remix', '2. One workflow original', '3. Wrong agent remix'], status: 'pending' }, 54_000),
    row('card', { type: 'card', card: 'question', question: 'Which inbox should I watch?', options: ['Work', 'Personal'], status: 'answered', answer: 'Work' }, 53_000),
    row('card', { type: 'card', card: 'connect', toolkit: 'slack', toolkitName: 'Slack', logo: 'https://logos.composio.dev/api/slack', reason: 'I need Slack to post the daily digest in #general.', status: 'pending' }, 52_000),
    row('event', { type: 'event', event: 'routine-created', text: 'Routine created: Daily digest · Weekdays at 9:00 AM' }, 51_000),
    row('notice', { type: 'notice', text: "You're at 80% of your weekly usage limit.", tone: 'warn' }, 50_000),
    row('message', { type: 'message', role: 'assistant', text: "The DM didn't go out. Here's the text to paste into a DM to **@alex**:\n\n```\nHey Alex, quick question. Any plans to support more than 3 seats?\n```\n\n> Tip: X only allows message requests from the app.\n\nInline `code` works too." }, 49_000),
  ];
  st.upsertRows(botId, gen, rows);
}
