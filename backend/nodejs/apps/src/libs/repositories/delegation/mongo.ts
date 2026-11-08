import mongoose from 'mongoose';
import { ServiceAccountDelegation } from '../../../modules/oauth_provider/schema/service-account-delegation.schema';
import { DelegationEvent } from '../../../modules/oauth_provider/schema/delegation-event.schema';
import { toPlain } from '../plain';
import type {
  DelegationEventDto,
  DelegationRepository,
  DelegationRowDto,
  NewDelegationEvent,
} from './types';

const Types = mongoose.Types;
const DUPLICATE_KEY = 11000;

const isDuplicateKeyError = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === DUPLICATE_KEY;

const rowDto = (row: unknown): DelegationRowDto =>
  toPlain<DelegationRowDto>(row);

const optionalId = (
  id: string | undefined,
): mongoose.Types.ObjectId | undefined =>
  id === undefined ? undefined : new Types.ObjectId(id);

export class MongoDelegationRepository implements DelegationRepository {
  async isAllowed(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean> {
    const row = await ServiceAccountDelegation.findOne({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
      subjectUserId: { $eq: subjectUserId },
    })
      .select('_id')
      .lean()
      .exec();
    return row !== null;
  }

  async countFor(orgId: string, serviceAccountId: string): Promise<number> {
    return ServiceAccountDelegation.countDocuments({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    });
  }

  async grant(input: {
    orgId: string;
    serviceAccountId: string;
    subjectUserId: string;
    grantedBy: string;
    reason?: string;
  }): Promise<{ row: DelegationRowDto; created: boolean }> {
    try {
      const row = await ServiceAccountDelegation.create({
        orgId: new Types.ObjectId(input.orgId),
        serviceAccountId: new Types.ObjectId(input.serviceAccountId),
        subjectUserId: new Types.ObjectId(input.subjectUserId),
        grantedBy: new Types.ObjectId(input.grantedBy),
        reason: input.reason,
      });
      return { row: rowDto(row), created: true };
    } catch (error) {
      // Only a duplicate is forgiven, and only because the unique index is
      // what raised it. Anything else has to surface.
      if (!isDuplicateKeyError(error)) {
        throw error;
      }
      const already = await ServiceAccountDelegation.findOne({
        orgId: { $eq: input.orgId },
        serviceAccountId: { $eq: input.serviceAccountId },
        subjectUserId: { $eq: input.subjectUserId },
      }).exec();
      if (already) {
        return { row: rowDto(already), created: false };
      }
      throw error;
    }
  }

  async listFor(
    orgId: string,
    serviceAccountId: string,
  ): Promise<DelegationRowDto[]> {
    const rows = await ServiceAccountDelegation.find({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    })
      .sort({ createdAt: -1, _id: -1 })
      .lean()
      .exec();
    return rows.map(rowDto);
  }

  async revoke(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean> {
    const removed = await ServiceAccountDelegation.findOneAndDelete({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
      subjectUserId: { $eq: subjectUserId },
    }).exec();
    return Boolean(removed);
  }

  async revokeAllFor(orgId: string, serviceAccountId: string): Promise<number> {
    const result = await ServiceAccountDelegation.deleteMany({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    }).exec();
    return result.deletedCount;
  }

  async recordEvent(event: NewDelegationEvent): Promise<void> {
    await DelegationEvent.create({
      orgId: new Types.ObjectId(event.orgId),
      serviceAccountId: new Types.ObjectId(event.serviceAccountId),
      subjectUserId: optionalId(event.subjectUserId),
      requestedSubject: event.requestedSubject.slice(0, 320),
      outcome: event.outcome,
      refusedReason: event.refusedReason,
      actedBy: optionalId(event.actedBy),
      note: event.note,
      scopes: event.scopes ?? [],
      accessTokenId: optionalId(event.accessTokenId),
      jti: event.jti,
      expiresAt: event.expiresAt,
    });
  }

  async listEvents(
    orgId: string,
    serviceAccountId: string | undefined,
    limit: number,
  ): Promise<DelegationEventDto[]> {
    const query: Record<string, unknown> = { orgId: { $eq: orgId } };
    if (serviceAccountId !== undefined) {
      query.serviceAccountId = { $eq: serviceAccountId };
    }
    const events = await DelegationEvent.find(query)
      .sort({ createdAt: -1, _id: -1 })
      .limit(limit)
      .lean()
      .exec();
    return events.map((e) => toPlain<DelegationEventDto>(e));
  }
}
