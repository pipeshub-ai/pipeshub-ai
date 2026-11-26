import { InternalServerError } from '../../../errors/http.errors';
import {
  ControlPlaneLiveness,
  LivenessObservation,
} from '../../../../modules/agent-harness/ports/control-plane-liveness';
import { deploymentUnit } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

const CONTROL_PLANE = 'control-plane';

/** On database time (`harness.liveness`, deployment-wide), so a Node replica's jumped clock cannot fake or hide a gap. */
export class PgControlPlaneLiveness implements ControlPlaneLiveness {
  constructor(private readonly time: PgTime = pgServerTime) {}

  observe(): Promise<LivenessObservation> {
    return deploymentUnit({ write: true }, async (k) => {
      const now = await this.time.now(k);
      const { rows } = await k.query<{
        seen_at: number | null;
        previous_seen_at: number | null;
        holdoff_until: number;
      }>(
        `/* pipeshub:harness.liveness_observe */ INSERT INTO harness.liveness AS l (id, seen_at, previous_seen_at, holdoff_until)
           VALUES ($1, $2, NULL, 0)
         ON CONFLICT (id) DO UPDATE SET previous_seen_at = l.seen_at, seen_at = excluded.seen_at
         RETURNING seen_at, previous_seen_at, holdoff_until`,
        [CONTROL_PLANE, now],
      );
      const row = rows[0];
      if (row === undefined)
        throw new InternalServerError('the liveness record did not answer');
      return {
        now: Number(row.seen_at),
        previousSeenAt: row.previous_seen_at,
        holdoffUntil: row.holdoff_until,
      };
    });
  }

  async holdOff(until: number): Promise<void> {
    // A holdoff ordered before the first sweeper tick must still hold: it is the record, seen never.
    await deploymentUnit({ write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.liveness_hold_off */ INSERT INTO harness.liveness AS l (id, seen_at, previous_seen_at, holdoff_until)
           VALUES ($1, NULL, NULL, $2)
         ON CONFLICT (id) DO UPDATE SET holdoff_until = GREATEST(l.holdoff_until, excluded.holdoff_until)`,
        [CONTROL_PLANE, until],
      ),
    );
  }
}
