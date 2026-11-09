import type { ActivityKind, LiveState, Row } from '@shared/types';

/** textKind → phrase table from docs/research/03-ui-design.md §2.4. Arrays rotate. */
const PHRASES: Record<string, string[]> = {
  thinking: ['Thinking it through', 'Mulling it over', 'Putting it together'],
  working: ['Getting it done', 'Making progress', 'Working on it', 'Handling it', 'Chipping away'],
  searching_web: ['Digging around'],
  reading_web: ['Doing some reading'],
  browsing_web: ['Clicking through pages'],
  reading_file: ['Reading the file'],
  drafting_file: ['Jotting it down'],
  organizing_files: ['Moving files over'],
  on_your_computer: ['Working on your computer'],
  on_its_computer: ['Clicking around'],
  running_commands: ['Running a few commands'],
  waiting_on_command: ['Waiting on results'],
  coding: ['Writing some code'],
  generating_photo: ['Sketching something up'],
  generating_video: ['Filming a clip'],
  messaging_assistant: ['Checking with another Bot'],
  messaging: ['Sending a message'],
  connecting_app: ['Working in a connected app'],
  waiting_for_you: ['Needs your input'],
  checking_usage: ['Checking your usage'],
  remembering: ['Making a note of it'],
};

const nice = (s?: string) => !!s && !/[_]/.test(s) && !/^[a-z0-9-]+$/.test(s);

/** Phrase for the activity line. `tick` rotates multi-phrase kinds. */
export function activityPhrase(live: LiveState, rows: Row[], tick: number): string {
  const a = live.activity;
  if (!a) return live.awaiting ? 'Needs your input' : 'Working on it';
  const kind = a.kind as ActivityKind;
  if (kind === 'messaging_named') {
    const running = [...rows].reverse().find((r) => r.body.type === 'tool-call' && r.body.status === 'running');
    const args = running?.body.type === 'tool-call' ? (running.body.args as Record<string, unknown> | undefined) : undefined;
    const name = typeof args?.bot_name === 'string' ? args.bot_name : nice(a.detail) ? a.detail : null;
    return name ? `Checking with ${name}` : 'Checking with another Bot';
  }
  if (kind === 'connecting_service') return nice(a.detail) ? `Working in ${a.detail}` : 'Working in a connected app';
  const list = PHRASES[kind] ?? PHRASES.working;
  return list[tick % list.length];
}
