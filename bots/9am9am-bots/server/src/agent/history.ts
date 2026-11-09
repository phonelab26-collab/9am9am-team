/**
 * Repair a Responses API history before sending it to the model.
 * If a turn was cut off mid-tool (server restart, interrupt, crash), the stored history can
 * contain function_call items with no matching function_call_output, which xAI rejects.
 * We add a synthetic output right after each orphaned call so the conversation stays valid.
 * Calls listed in `pendingCallIds` (waiting on a user approval) are left untouched only if
 * they are at the very end of the history; anywhere else they are repaired too.
 */
export function repairHistory(items: any[], pendingCallIds: Set<string> = new Set()): any[] {
  const answered = new Set(items.filter((it) => it?.type === 'function_call_output').map((it) => it.call_id));
  const out: any[] = [];
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    out.push(it);
    if (it?.type !== 'function_call' || answered.has(it.call_id)) continue;
    // Keep a trailing pending-approval batch as-is (the loop will add outputs when resolved).
    const restAreCalls = items.slice(i + 1).every((x) => x?.type === 'function_call' || x?.type === 'reasoning');
    if (pendingCallIds.has(it.call_id) && restAreCalls) continue;
    out.push({
      type: 'function_call_output',
      call_id: it.call_id,
      output: 'This tool call was interrupted before it finished (the run was stopped or restarted). Its result is unknown; check the current state before retrying.',
    });
    answered.add(it.call_id);
  }
  return out;
}
