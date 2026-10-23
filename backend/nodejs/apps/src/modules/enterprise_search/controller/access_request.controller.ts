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
import type { IAccessRequest } from '../schema/access.request.schema';
import { ChatSession } from '../schema/chat.session.schema';
import EnterpriseSemanticSearch from '../schema/search.schema';
import { getMessages } from '../utils/utils';
import { ProjectService } from '../../projects/services/project.service';
import {
  callRecordAccess,
  collectMessageRecordIds,
  collectSearchRecordIds,
  messageAuthorId,
  readableRecordIds,
  redactedSearchCandidates,
  redactedSourceCandidates,
  type ViewerMessage,
} from '../utils/viewer-redaction';

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
  } catch (error: unknown) {
    // The request is stored either way; the owner also finds it in their inbox.
    logger.warn('Access request notification failed', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

type RequestRow = Pick<
  IAccessRequest,
  | 'recordId'
  | 'recordName'
  | 'collectionName'
  | 'kind'
  | 'status'
  | 'requesterId'
  | 'createdAt'
> & { _id: unknown };

const present = (
  r: RequestRow,
  forRequester = false,
): Record<string, unknown> => ({
  id: String(r._id),
  recordId: r.recordId,
  // The requester cannot read the record: its name and collection stay server-side.
  ...(forRequester
    ? {}
    : { recordName: r.recordName, collectionName: r.collectionName }),
  kind: r.kind,
  // `void` is internal: to the requester it reads as a request waiting for an owner.
  status: r.status === 'void' ? 'pending' : r.status,
  requesterId: String(r.requesterId),
  createdAt: r.createdAt,
});

/**
 * The record ids this viewer was shown as redacted sources in the chat or search they name, or
 * none when they cannot open it. Only these may be asked for, so the route says nothing about
 * a record the viewer was never shown.
 */
const shownRedactedIds = async (
  appConfig: AppConfig,
  orgId: string,
  userId: string,
  from: { conversationId?: string; searchId?: string },
): Promise<Set<string>> => {
  const org = new mongoose.Types.ObjectId(orgId);
  const user = new mongoose.Types.ObjectId(userId);
  if (from.conversationId) {
    const projects = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );
    const session = await ChatSession.findOne({
      _id: new mongoose.Types.ObjectId(from.conversationId),
      orgId: org,
      isDeleted: false,
      $or: [
        { userId: user },
        { isShared: true, 'sharedWith.userId': user },
        ...(projects.length > 0
          ? [{ projectId: { $in: projects }, projectVisibility: 'project' }]
          : []),
      ],
    }).lean();
    if (!session) return new Set();
    const messages = (await getMessages(session._id, {
      orgId,
      populateCitations: true,
    })) as ViewerMessage[];
    const initiator = (
      session.initiator as { toString(): string } | undefined
    )?.toString();
    const others = messages.filter(
      (m) => messageAuthorId(m, initiator) !== userId,
    );
    const readable = await readableRecordIds(
      appConfig,
      { userId, orgId },
      collectMessageRecordIds(others),
    );
    return redactedSourceCandidates(messages, initiator, userId, readable);
  }
  const search = await EnterpriseSemanticSearch.findOne({
    _id: new mongoose.Types.ObjectId(from.searchId),
    orgId: org,
    $or: [{ userId: user }, { isShared: true, 'sharedWith.userId': user }],
  })
    .populate({ path: 'citationIds', model: 'citation', select: '-__v' })
    .lean();
  if (!search) return new Set();
  const readable = await readableRecordIds(
    appConfig,
    { userId, orgId },
    collectSearchRecordIds(search),
  );
  return redactedSearchCandidates(search, userId, readable);
};

/**
 * POST /api/v1/access-requests  { recordId, conversationId | searchId }
 * Org-scoped, rate-limited per user. The record's kind and owner come from the
 * graph, never from the client.
 *
 * The record must be one the viewer was shown as redacted in the named chat or search. Any
 * other id (unknown, hidden, not in that chat, or readable) gets the same answer as a first
 * request for a native record: `201` with a `pending` request. Nothing is notified for it, but
 * it is stored as an internal `void` row, so repeats, the hourly quota and the request shape
 * behave exactly as for a real one.
 */
export const createAccessRequest =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user as { userId: string; orgId: string };
      const { recordId, conversationId, searchId } = req.body as {
        recordId: string;
        conversationId?: string;
        searchId?: string;
      };
      const requesterId = new mongoose.Types.ObjectId(userId);
      const org = new mongoose.Types.ObjectId(orgId);

      const shown = await shownRedactedIds(appConfig, orgId, userId, {
        conversationId,
        searchId,
      });
      // The query service is asked about the record only when the viewer was shown it as
      // redacted (it is then unreadable to them by construction). An id they were not shown
      // is never looked up, so nothing about it is learned or revealed.
      const isShown = shown.has(recordId);
      const check = isShown
        ? await callRecordAccess<{ deniedDetails?: DeniedDetail[] }>(
            appConfig,
            { userId, orgId },
            'access',
            { recordIds: [recordId], includeOwners: true },
          )
        : undefined;
      const detail = check?.deniedDetails?.find((d) => d.recordId === recordId);
      const owners = [...new Set(detail?.ownerUserIds ?? [])].filter((id) =>
        mongoose.isValidObjectId(id),
      );
      const genuine =
        !!detail &&
        detail.kind !== 'unknown' &&
        (detail.kind !== 'native' || owners.length > 0);

      // One open request per collection (or per record outside one), for every answer alike. A
      // real request matches only real rows and an unseen id only its own void row, so a void
      // row never swallows a request that is later genuine.
      const openKey = `${genuine ? 'open' : 'void'}:${
        genuine && detail.collection
          ? `collection:${detail.collection.id}`
          : `record:${recordId}`
      }`;
      const sourceUrl =
        genuine && detail.kind === 'connector' && detail.webUrl
          ? { sourceUrl: detail.webUrl }
          : {};
      const existing = await AccessRequest.findOne({
        orgId: org,
        requesterId,
        openKey,
      }).lean();
      if (existing) {
        res
          .status(201)
          .json({ request: present(existing, true), ...sourceUrl });
        return;
      }

      const recent = await AccessRequest.countDocuments({
        orgId: org,
        requesterId,
        createdAt: { $gte: new Date(Date.now() - HOUR_MS) },
      });
      if (recent >= ACCESS_REQUESTS_PER_HOUR) {
        throw new TooManyRequestsError(
          'Too many access requests. Try again later.',
        );
      }

      let created;
      try {
        created = await AccessRequest.create({
          orgId: org,
          requesterId,
          recordId,
          openKey,
          ...(genuine
            ? {
                recordName: detail.recordName,
                collectionId: detail.collection?.id,
                collectionName: detail.collection?.name,
                kind: detail.kind,
                ownerUserIds: owners.map(
                  (id) => new mongoose.Types.ObjectId(id),
                ),
                status: detail.kind === 'native' ? 'pending' : 'forwarded',
              }
            : { kind: 'native', ownerUserIds: [], status: 'void' }),
        });
      } catch (error) {
        // An identical request opened the row first (the unique open key): answer with it.
        if ((error as { code?: number }).code !== 11000) throw error;
        const winner = await AccessRequest.findOne({
          orgId: org,
          requesterId,
          openKey,
        }).lean();
        if (!winner) throw error;
        res.status(201).json({ request: present(winner, true), ...sourceUrl });
        return;
      }
      if (genuine) {
        const requester = await Users.findById(requesterId)
          .select('fullName email')
          .lean();
        const requesterName =
          requester?.fullName || requester?.email || 'A colleague';
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
      }
      res.status(201).json({ request: present(created, true), ...sourceUrl });
    } catch (error) {
      next(error);
    }
  };

/** GET /api/v1/access-requests  : pending native requests addressed to the caller. */
export const listIncomingAccessRequests = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
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
): Promise<void> => {
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
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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

      // Claim the decision before acting, so two owners deciding at once cannot both act.
      const decidedBy = new mongoose.Types.ObjectId(userId);
      const decided = await AccessRequest.findOneAndUpdate(
        { _id: request._id, status: 'pending' },
        {
          $set: {
            status: decision === 'approve' ? 'approved' : 'denied',
            decidedBy,
            decidedAt: new Date(),
          },
          ...(decision === 'deny' ? { $unset: { openKey: 1 } } : {}),
        },
        { new: true },
      ).lean();
      if (!decided) {
        throw new ConflictError('This request was already decided');
      }

      if (decision === 'approve') {
        try {
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
        } catch (error) {
          // Release the claim so the request can be decided again.
          await AccessRequest.updateOne(
            { _id: request._id, status: 'approved', decidedBy },
            {
              $set: { status: 'pending' },
              $unset: { decidedBy: 1, decidedAt: 1 },
            },
          );
          throw error;
        }
        await AccessRequest.updateOne(
          { _id: request._id },
          { $unset: { openKey: 1 } },
        );
      }

      const target = request.collectionId
        ? `the collection "${request.collectionName ?? ''}"`
        : `"${request.recordName ?? 'the record'}"`;

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
      res.status(200).json({ request: present(decided) });
    } catch (error) {
      next(error);
    }
  };
