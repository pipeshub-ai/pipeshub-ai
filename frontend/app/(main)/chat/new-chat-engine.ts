/**
 * The coding engine a new chat's first message named (CMDUI2), read back once the turn's route is known. Kept
 * apart from the sandbox commands so the stream code imports only this.
 */
import { ChatApi } from './api';
import { useBuiltinResults, type WorkspaceSession } from './builtin-results';

/**
 * After a new chat's first message named an engine (CMDUI2): the server saved it on the chat before the run
 * started, or refused it and ran the message on the default. Reading the chat's own choice back says which; one
 * that did not hold gets `/engine`'s plain "not saved" line under the message. `session` null: the classic loop
 * took the turn, and it has no coding engine.
 */
export async function confirmNewChatEngine(rowKey: string, session: WorkspaceSession | null, engine: string): Promise<void> {
  let held = false;
  if (session) {
    try {
      held = (await ChatApi.getSessionSandbox(session)).codingEngine === engine;
    } catch {
      held = false;
    }
  }
  if (held) return;
  const results = useBuiltinResults.getState();
  results.add(rowKey, { typed: `/engine ${engine}`, afterPairs: Math.max(results.pairs[rowKey] ?? 0, 1), result: { kind: 'engineNotSaved', engine } });
}
