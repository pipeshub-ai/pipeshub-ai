import { ServiceUnavailableError } from '../errors/http.errors';
import { mongoFenceArmed } from './fence';
import { mongoDrainRequested } from './mongo';
import { docStore } from './store-select';

/** The system is being moved to a new database: no new chat turn starts for a minute or two. */
export class MaintenanceError extends ServiceUnavailableError {
  constructor(retryAfterSeconds = 120) {
    super('The system is being updated; please try again in a minute.', {
      retryAfter: String(retryAfterSeconds),
    });
  }
}

/**
 * Called before a chat turn claims its run. During a move to a new document store the migration tool asks the running
 * application to take no new turns (the fence document says `draining`), so that the turns in flight can finish and be saved
 * before the application stops. A no-op on PostgreSQL and while the fence is off.
 */
export async function assertAcceptingRuns(): Promise<void> {
  if (docStore() !== 'mongo' || !mongoFenceArmed()) return;
  if (await mongoDrainRequested()) throw new MaintenanceError();
}

/**
 * For an HTTP handler that starts a chat turn: when the move to a new store has asked for no new turns, answers 503 with
 * `Retry-After` itself (before any event-stream header is sent) and returns true. The handlers take no `next`, so a throw here would
 * be an unhandled rejection and the client would wait for ever.
 */
export async function refuseIfDraining(res: {
  headersSent: boolean;
  status(code: number): {
    set(name: string, value: string): { json(body: unknown): unknown };
  };
}): Promise<boolean> {
  try {
    await assertAcceptingRuns();
    return false;
  } catch (e) {
    if (!(e instanceof MaintenanceError) || res.headersSent) throw e;
    res
      .status(503)
      .set('Retry-After', String(e.metadata?.retryAfter ?? '120'))
      .json({
        error: { code: 'HTTP_SERVICE_UNAVAILABLE', message: e.message },
      });
    return true;
  }
}
