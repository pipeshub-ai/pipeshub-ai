import mongoose, { UpdateQuery } from 'mongoose';
import Citation from '../../../modules/enterprise_search/schema/citation.schema';
import EnterpriseSemanticSearch, {
  IEnterpriseSemanticSearch,
} from '../../../modules/enterprise_search/schema/search.schema';
import { BadRequestError } from '../../errors/http.errors';
import { sessionOf } from '../mongo';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import type {
  CitationDto,
  NewSemanticSearch,
  SearchListQuery,
  SearchQuery,
  SearchSharing,
  SemanticSearchDto,
  SemanticSearchRepository,
} from './types';

const oid = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);

/** The access filter of the old `buildFilter` for searches, built from typed input. */
function visibleFilter(
  orgId: string,
  userId: string,
  q: SearchQuery,
  id?: string,
  archived?: boolean,
): Record<string, unknown> {
  const owned = q.owned ?? true;
  const shared = q.shared ?? true;
  if (!owned && !shared) {
    throw new BadRequestError('Either owned or shared must be true');
  }
  const filter: Record<string, unknown> = {
    orgId: oid(orgId),
    isDeleted: false,
    isArchived: false,
    $or: [
      ...(owned ? [{ userId: oid(userId) }] : []),
      ...(shared
        ? [{ $and: [{ isShared: true }, { 'sharedWith.userId': oid(userId) }] }]
        : []),
    ],
  };

  if (id) {
    filter._id = oid(id);
  }

  if (q.search) {
    filter.$and = [{ $or: [{ title: { $regex: q.search, $options: 'i' } }] }];
  }

  if (q.startDate || q.endDate) {
    filter.createdAt = {
      ...(q.startDate ? { $gte: q.startDate } : {}),
      ...(q.endDate ? { $lte: q.endDate } : {}),
    };
  }

  if (q.isShared !== undefined) {
    filter.isShared = q.isShared;
  }

  // Callers that read archived rows replaced the flag after building the filter.
  if (archived !== undefined) {
    filter.isArchived = archived;
  }
  return filter;
}

const dto = (doc: unknown): SemanticSearchDto =>
  toPlain<SemanticSearchDto>(doc, { json: true });

export class MongoSemanticSearchRepository implements SemanticSearchRepository {
  async create(input: NewSemanticSearch, tx?: Tx): Promise<SemanticSearchDto> {
    const session = sessionOf(tx);
    let citationIds;
    if (input.hits) {
      const saved = await Citation.insertMany(input.hits, {
        ordered: true,
        session,
      });
      citationIds = saved.map((citation) => citation._id);
    }
    const doc = await new EnterpriseSemanticSearch({
      query: input.query,
      limit: input.limit,
      orgId: input.orgId,
      userId: input.userId,
      citationIds,
      records: input.records,
    }).save({ session });
    return dto(doc);
  }

  async findVisible(
    orgId: string,
    viewerId: string,
    id: string,
    opts: {
      archived?: boolean;
      populateCitations?: boolean;
      query?: SearchQuery;
    } = {},
  ): Promise<SemanticSearchDto | null> {
    const filter = visibleFilter(
      orgId,
      viewerId,
      opts.query ?? {},
      id,
      opts.archived,
    );
    if (opts.populateCitations) {
      const rows = await EnterpriseSemanticSearch.find(filter)
        .populate({ path: 'citationIds', model: 'citation', select: '-__v' })
        .lean()
        .exec();
      return rows[0] ? toPlain<SemanticSearchDto>(rows[0]) : null;
    }
    const doc = await EnterpriseSemanticSearch.findOne(filter);
    return doc ? dto(doc) : null;
  }

  async listVisible(
    orgId: string,
    viewerId: string,
    q: SearchListQuery,
  ): Promise<{ rows: SemanticSearchDto[]; total: number }> {
    const filter = visibleFilter(orgId, viewerId, q);
    const sort: Record<string, 1 | -1> = {
      [q.sort?.field ?? 'lastActivityAt']: q.sort?.direction ?? -1,
      // ties follow the sort's direction, as the PostgreSQL store orders them
      _id: q.sort?.direction ?? -1,
    };
    const [docs, total] = await Promise.all([
      EnterpriseSemanticSearch.find(filter)
        .sort(sort)
        .skip(q.skip)
        .limit(q.limit)
        .exec(),
      EnterpriseSemanticSearch.countDocuments(filter),
    ]);
    return { rows: docs.map(dto), total };
  }

  async setSharing(
    id: string,
    sharing: SearchSharing,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const update: Record<string, unknown> = { sharedWith: sharing.sharedWith };
    if (sharing.isShared !== undefined) update.isShared = sharing.isShared;
    if (sharing.shareLink !== undefined) update.shareLink = sharing.shareLink;
    const doc = await EnterpriseSemanticSearch.findByIdAndUpdate(id, update, {
      session: sessionOf(tx),
    });
    return doc ? dto(doc) : null;
  }

  async unshare(
    id: string,
    remaining: SearchSharing['sharedWith'],
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    // An undefined value is dropped from an update, so the link is unset explicitly.
    const update: UpdateQuery<IEnterpriseSemanticSearch> = {
      sharedWith: remaining,
      ...(remaining.length === 0
        ? { isShared: false, $unset: { shareLink: 1 } }
        : {}),
    };
    const doc = await EnterpriseSemanticSearch.findByIdAndUpdate(id, update, {
      session: sessionOf(tx),
      returnDocument: 'after',
    });
    return doc ? dto(doc) : null;
  }

  async archive(
    id: string,
    byUserId: string,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const doc = await EnterpriseSemanticSearch.findByIdAndUpdate(
      id,
      {
        $set: {
          isArchived: true,
          archivedBy: byUserId,
          lastActivityAt: Date.now(),
        },
      },
      { session: sessionOf(tx) },
    ).exec();
    return doc ? dto(doc) : null;
  }

  async unarchive(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const filter = visibleFilter(orgId, viewerId, query ?? {}, id, true);
    const doc = await EnterpriseSemanticSearch.findOneAndUpdate(
      filter,
      {
        $set: {
          isArchived: false,
          archivedBy: null,
          lastActivityAt: Date.now(),
        },
      },
      { session: sessionOf(tx) },
    ).exec();
    return doc ? dto(doc) : null;
  }

  async delete(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const session = sessionOf(tx);
    const filter = visibleFilter(orgId, viewerId, query ?? {}, id);
    const doc = await EnterpriseSemanticSearch.findOneAndDelete(filter, {
      session,
    });
    if (doc === null) return null;
    await Citation.deleteMany({ _id: { $in: doc.citationIds } }, { session });
    return dto(doc);
  }

  async deleteVisible(
    orgId: string,
    viewerId: string,
    q: SearchQuery,
    tx?: Tx,
  ): Promise<number> {
    const session = sessionOf(tx);
    const filter = visibleFilter(orgId, viewerId, q);
    let query = EnterpriseSemanticSearch.find(filter);
    if (session) query = query.session(session);
    const searches = await query.lean().exec();
    if (searches.length === 0) return 0;
    const citationIds = searches.flatMap((search) => search.citationIds);
    await EnterpriseSemanticSearch.deleteMany(filter, { session });
    await Citation.deleteMany({ _id: { $in: citationIds } }, { session });
    return searches.length;
  }

  async findCitations(orgId: string, ids: string[]): Promise<CitationDto[]> {
    const docs = ids.length
      ? await Citation.find({ _id: { $in: ids }, 'metadata.orgId': orgId })
          .select('-__v')
          .lean()
      : [];
    return docs.map((d) => toPlain<CitationDto>(d));
  }
}
