import {
  holdsNothing,
  QuotaReservation,
  QuotaReservationRequest,
  reservationOf,
} from '../../../../modules/agent-harness/domain/admission-quotas';
import { AgentAdmissionQuotaModel } from '../execution/schema/execution.schema';
import type { AdmissionQuotaLedger } from './hold';

export { AdmissionQuotaHold, TwinInFlight } from './hold';
export type { AdmissionQuotaLedger, AdmissionQuotaMetrics } from './hold';

const present = (path: string): Record<string, unknown> => ({
  $eq: [{ $type: path }, 'object'],
});

/**
 * An org's or an actor's quota document, written by one update of its own and
 * never in the admission's transaction: a second writer of a document waits a
 * moment here, where inside a transaction it aborts and runs again (PERFB-F1;
 * a person's burst of 500 cost 36 transaction attempts each, DOS-MONGO-1). The
 * updates are `reserveQuota` and `releaseQuota` of the domain, as one
 * pipeline each.
 */
export class MongoAdmissionQuotaLedger implements AdmissionQuotaLedger {
  async reserve(
    key: string,
    request: QuotaReservationRequest,
  ): Promise<QuotaReservation> {
    const { id, runId, now, limit, windowMs, holdMs, admission } = request;
    const hold = {
      id,
      runId,
      at: now,
      windowStart: '$windowStart',
      ...(admission !== undefined ? { admission } : {}),
    };
    // The domain's `twinOf`: a live hold of another instance of the same admission.
    const twin =
      admission === undefined
        ? false
        : {
            $gt: [
              {
                $size: {
                  $filter: {
                    input: '$holds',
                    cond: {
                      $and: [
                        { $eq: ['$$this.admission', admission] },
                        { $ne: ['$$this.id', id] },
                      ],
                    },
                  },
                },
              },
              0,
            ],
          };
    const document = await AgentAdmissionQuotaModel.findOneAndUpdate(
      { _id: key },
      [
        {
          $set: {
            lapsed: {
              $or: [
                { $eq: [{ $type: '$windowStart' }, 'missing'] },
                { $lte: ['$windowStart', now - windowMs] },
              ],
            },
            holds: {
              $filter: {
                input: { $ifNull: ['$holds', []] },
                cond: { $gt: ['$$this.at', now - holdMs] },
              },
            },
          },
        },
        {
          $set: {
            windowStart: { $cond: ['$lapsed', now, '$windowStart'] },
            count: { $cond: ['$lapsed', 0, '$count'] },
            mine: {
              $first: {
                $filter: { input: '$holds', cond: { $eq: ['$$this.id', id] } },
              },
            },
            twin,
          },
        },
        {
          $set: {
            counted: {
              $and: [
                present('$mine'),
                { $eq: ['$mine.windowStart', '$windowStart'] },
              ],
            },
          },
        },
        {
          $set: {
            granted: {
              $and: [
                { $not: ['$twin'] },
                { $or: ['$counted', { $lt: ['$count', limit] }] },
              ],
            },
          },
        },
        {
          $set: {
            count: {
              $cond: [
                { $and: ['$granted', { $not: ['$counted'] }] },
                { $add: ['$count', 1] },
                '$count',
              ],
            },
            holds: {
              $cond: [
                '$granted',
                {
                  $cond: [
                    present('$mine'),
                    {
                      $map: {
                        input: '$holds',
                        in: {
                          $cond: [{ $eq: ['$$this.id', id] }, hold, '$$this'],
                        },
                      },
                    },
                    { $concatArrays: ['$holds', [hold]] },
                  ],
                },
                {
                  $filter: {
                    input: '$holds',
                    cond: { $ne: ['$$this.id', id] },
                  },
                },
              ],
            },
          },
        },
        { $unset: ['lapsed', 'mine', 'counted', 'granted', 'twin'] },
      ],
      { updatePipeline: true, upsert: true, returnDocument: 'after' },
    ).lean();
    return reservationOf(
      {
        windowStart: document.windowStart,
        count: document.count,
        holds: document.holds ?? [],
      },
      request,
    );
  }

  /** `refund`: the admission did not happen, so its place in the window is given back. */
  async release(key: string, id: string, refund: boolean): Promise<void> {
    const mine = {
      $first: {
        $filter: {
          input: { $ifNull: ['$holds', []] },
          cond: { $eq: ['$$this.id', id] },
        },
      },
    };
    const left = await AgentAdmissionQuotaModel.findOneAndUpdate(
      { _id: key },
      [
        { $set: { mine } },
        {
          $set: {
            count: {
              $cond: [
                {
                  $and: [
                    refund,
                    present('$mine'),
                    { $eq: ['$mine.windowStart', '$windowStart'] },
                  ],
                },
                { $subtract: ['$count', 1] },
                '$count',
              ],
            },
            holds: {
              $filter: {
                input: { $ifNull: ['$holds', []] },
                cond: { $ne: ['$$this.id', id] },
              },
            },
          },
        },
        { $unset: ['mine'] },
      ],
      { updatePipeline: true, returnDocument: 'after' },
    ).lean();
    if (
      left !== null &&
      holdsNothing({
        count: left.count,
        windowStart: left.windowStart,
        holds: left.holds ?? [],
      })
    ) {
      // Only while it is still empty: a reservation since then keeps it.
      await AgentAdmissionQuotaModel.deleteOne({
        _id: key,
        count: 0,
        holds: { $size: 0 },
      });
    }
  }
}
