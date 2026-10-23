import { NextFunction, Response } from 'express';
import mongoose from 'mongoose';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  NotFoundError,
  TooManyRequestsError,
} from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { NotificationContainer } from '../../notification/container/notification.container';
import { Notifications } from '../../notification/schema/notification.schema';
import { AccessRequest } from '../schema/access.request.schema';
import { Users } from '../../user_management/schema/users.schema';
import { callRecordAccess, readableRecordIds } from '../utils/viewer-redaction';

const logger = Logger.getInstance({ service: 'Access Requests' });

/** Requests one user may open per hour; the owner is notified once per record. */
export const ACCESS_REQUESTS_PER_HOUR = 20;
const HOUR_MS = 60 * 60 * 1000;

interface DeniedDetail {
  recordId: string;
  kind: 'native' | 'connector' | 'unknown';
  recordName?: string;
  webUrl?: string | null;
  /** Set for a record of a collection: access exists only at the collection. */
  collection?: { id: string; name?: string } | null;
  ownerUserIds?: string[];
}

const notify = async (
  orgId: string,
  userId: string,
  type: string,
  title: string,
  message: string,
  payload: Record<string, unknown>,
): Promise<void> => {
  try {
    const saved = await Notifications.create({
      orgId: new mongoose.Types.ObjectId(orgId),
      assignedTo: new mongoose.Types.ObjectId(userId),
      type,
      title,
      message,
      severity: 'info',
      status: 'unread',
      originService: 'AI Service',
      redirectLink: '/notifications',
      payload,
      isDeleted: false,
    });
    NotificationContainer.getNotificationService()?.sendToUser(
      userId,
      'newNotification',
      saved.toObject(),
    );
  } catch (error: any) {
    // The request is stored either way; the owner also finds it in their inbox.
    logger.warn('Access request notification failed', { error: error.message });
  }
};

const present = (r: any, forRequester = false) => ({
  id: String(r._id),
  recordId: r.recordId,
  // The requester cannot read the record: its name and collection stay server-side.
  ...(forRequester
    ? {}
    : { recordName: r.recordName, collectionName: r.collectionName }),
  kind: r.kind,
  status: r.status,
  requesterId: String(r.requesterId),
  createdAt: r.createdAt,
});

/**
 * POST /api/v1/access-requests  { recordId }
 * Org-scoped, rate-limited per user. The record's kind and owner come from the
 * graph, never from the client.
 */
export const createAccessRequest =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user as { userId: string; orgId: string };
      const { recordId } = req.body as { recordId: string };
      const requesterId = new mongoose.Types.ObjectId(userId);
      const org = new mongoose.Types.ObjectId(orgId);

      // Recorded per scope: one open request per collection (or per record outside one).
      const check = await callRecordAccess<{
        accessible: string[];
        deniedDetails?: DeniedDetail[];
      }>(appConfig, { userId, orgId }, 'access', {
        recordIds: [recordId],
        includeOwners: true,
      });
      if (check.accessible?.includes(recordId)) {
        res.status(200).json({ alreadyAccessible: true });
        return;
      }
      const detail = check.deniedDetails?.find((d) => d.recordId === recordId);
      if (!detail || detail.kind === 'unknown') {
        throw new NotFoundError('Record not found');
      }
      const owners = [...new Set(detail.ownerUserIds ?? [])].filter((id) =>
        mongoose.isValidObjectId(id),
      );
      if (detail.kind === 'native' && owners.length === 0) {
        throw new ConflictError('Nobody can approve access to this record.');
      }

      const scope = detail.collection
        ? { collectionId: detail.collection.id }
        : { recordId };
      const existing = await AccessRequest.findOne({
        orgId: org,
        requesterId,
        ...scope,
        status: { $in: ['pending', 'forwarded'] },
      }).lean();
      if (existing) {
        res.status(200).json({ request: present(existing, true), duplicate: true });
        return;
      }

      const recent = await AccessRequest.countDocuments({
        orgId: org,
        requesterId,
        createdAt: { $gte: new Date(Date.now() - HOUR_MS) },
      });
      if (recent >= ACCESS_REQUESTS_PER_HOUR) {
        throw new TooManyRequestsError('Too many access requests. Try again later.');
      }

      const requester = await Users.findById(requesterId)
        .select('fullName email')
        .lean();
      const requesterName = requester?.fullName || requester?.email || 'A colleague';
      const created = await AccessRequest.create({
        orgId: org,
        requesterId,
        recordId,
        recordName: detail.recordName,
        collectionId: detail.collection?.id,
        collectionName: detail.collection?.name,
        kind: detail.kind,
        ownerUserIds: owners.map((id) => new mongoose.Types.ObjectId(id)),
        status: detail.kind === 'native' ? 'pending' : 'forwarded',
      });
      const what = detail.collection
        ? `the collection "${detail.collection.name ?? ''}"`
        : `"${detail.recordName ?? 'a record'}"`;
      for (const ownerId of owners) {
        await notify(
          orgId,
          ownerId,
          'accessRequest',
          'Access requested',
          detail.kind === 'connector'
            ? `${requesterName} asked for access to ${what} in its source system.`
            : `${requesterName} asked for read access to ${what}.`,
          {
            requestId: String(created._id),
            requesterName,
            collectionName: detail.collection?.name,
          },
        );
      }
      res.status(201).json({
        request: present(created, true),
        // Connector records: the viewer finishes at the source; nothing is granted here.
        ...(detail.kind === 'connector' && detail.webUrl
          ? { sourceUrl: detail.webUrl }
          : {}),
      });
    } catch (error) {
      next(error);
    }
  };

/** GET /api/v1/access-requests  : pending native requests addressed to the caller. */
export const listIncomingAccessRequests = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  try {
    const { userId, orgId } = req.user as { userId: string; orgId: string };
    const rows = await AccessRequest.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      ownerUserIds: new mongoose.Types.ObjectId(userId),
      status: 'pending',
    })
      .sort({ createdAt: -1 })
      .limit(100)
      .lean();
    res.status(200).json({ requests: rows.map((r) => present(r)) });
  } catch (error) {
    next(error);
  }
};

/** GET /api/v1/access-requests/:requestId : an owner who may decide it, to show its state. */
export const getAccessRequest = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  try {
    const { userId, orgId } = req.user as { userId: string; orgId: string };
    const request = await AccessRequest.findOne({
      _id: req.params.requestId,
      orgId: new mongoose.Types.ObjectId(orgId),
      ownerUserIds: new mongoose.Types.ObjectId(userId),
    }).lean();
    if (!request) throw new NotFoundError('Access request not found');
    res.status(200).json({ request: present(request) });
  } catch (error) {
    next(error);
  }
};

/** POST /api/v1/access-requests/:requestId/{approve,deny} : owner only. */
export const decideAccessRequest =
  (appConfig: AppConfig, decision: 'approve' | 'deny') =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user as { userId: string; orgId: string };
      const request = await AccessRequest.findOne({
        _id: req.params.requestId,
        orgId: new mongoose.Types.ObjectId(orgId),
      });
      if (!request) throw new NotFoundError('Access request not found');
      if (!request.ownerUserIds.some((id) => String(id) === userId)) {
        throw new ForbiddenError(
          'Only an owner of the record or collection can decide this request',
        );
      }
      if (request.kind !== 'native') {
        throw new BadRequestError(
          'Access to a connector record is granted in its source system',
        );
      }
      if (request.status !== 'pending') {
        throw new ConflictError('This request was already decided');
      }

      if (decision === 'approve') {
        await callRecordAccess(appConfig, { userId, orgId }, 'access-grant', {
          userId: String(request.requesterId),
          recordIds: [request.recordId],
        });
        // Python grants only what the owner owns; confirm before saying yes.
        const readable = await readableRecordIds(
          appConfig,
          { userId: String(request.requesterId), orgId },
          [request.recordId],
        );
        if (!readable.has(request.recordId)) {
          throw new ConflictError('Access could not be granted');
        }
      }

      const target = request.collectionId
        ? `the collection "${request.collectionName ?? ''}"`
        : `"${request.recordName ?? 'the record'}"`;
      request.status = decision === 'approve' ? 'approved' : 'denied';
      request.decidedBy = new mongoose.Types.ObjectId(userId);
      request.decidedAt = new Date();
      await request.save();

      await notify(
        orgId,
        String(request.requesterId),
        'accessRequestDecision',
        decision === 'approve' ? 'Access granted' : 'Access request declined',
        decision === 'approve'
          ? `You can now read ${target}. Refresh the shared chat.`
          : `Your request for ${target} was declined.`,
        { requestId: String(request._id) },
      );
      res.status(200).json({ request: present(request) });
    } catch (error) {
      next(error);
    }
  };
