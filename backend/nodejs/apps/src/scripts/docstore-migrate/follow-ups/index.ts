import { CONVERSATION_FOLLOW_UPS } from './conversation';
import { IDENTITY_FOLLOW_UPS } from './identity';
import type { FollowUp, FollowUpHandler } from './types';
import type { MigrationRuntime } from '../runtime';

const HANDLERS: Record<string, FollowUpHandler> = {
  ...IDENTITY_FOLLOW_UPS,
  ...CONVERSATION_FOLLOW_UPS,
};

/** Runs follow-ups once their copy unit has committed. An unknown kind is an error: nothing may be silently dropped. */
export async function runFollowUps(
  rt: MigrationRuntime,
  list: FollowUp[],
): Promise<void> {
  for (const f of list) {
    const h = HANDLERS[f.kind];
    if (!h) throw new Error(`no handler for follow-up '${f.kind}'`);
    await h(rt, f);
  }
}
