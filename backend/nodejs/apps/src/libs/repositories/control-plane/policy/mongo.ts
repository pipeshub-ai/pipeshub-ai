import type { ClientSession } from 'mongoose';
import { isDuplicateKeyError } from '../../../utils/mongo.utils';
import {
  ControlPlaneRevisionConflictError,
  ControlPlaneUnavailableError,
} from '../../../../modules/control-plane/domain/errors';
import type { PolicyLayer } from '../../../../modules/control-plane/domain/policy-alias';
import {
  readPolicyLayer,
  scopeKindOf,
} from '../../../../modules/control-plane/domain/policy-layer';
import { InvalidObject } from '../../../../modules/control-plane/domain/store-errors';
import { writeEach } from '../../../../modules/control-plane/domain/policy-layer';
import type {
  LayerRead,
  LayerWrite,
  PolicyLayers,
  PolicyOrgSource,
  PolicyRevision,
  PolicyStore,
  PolicyStorePurge,
  VersionedLayer,
} from '../../../../modules/control-plane/ports/policy-store';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import { policyWritten } from '../../../../modules/control-plane/ports/policy-write-observers';
import {
  PolicyHistoryDocument,
  PolicyHistoryModel,
  PolicyLayerDocument,
  PolicyLayerModel,
} from './schema/policy.schema';

const idOf = (orgId: string, scope: string): string => `${orgId}:${scope}`;
const sessionOf = (tx: StoreTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;

const ORG_SCAN_PAGE = 500;
const UNAVAILABLE: LayerRead = {
  status: 'unavailable',
  code: 'CP_UNAVAILABLE',
};

function parsed(body: string | null): PolicyLayer | null | undefined {
  if (body === null) return null;
  try {
    return readPolicyLayer(JSON.parse(body)) ?? undefined;
  } catch {
    return undefined;
  }
}

function layerRead(doc: PolicyLayerDocument | undefined): LayerRead {
  if (doc === undefined) return { status: 'ok', layer: null, revision: 0 };
  const layer = parsed(doc.body);
  return layer === undefined
    ? UNAVAILABLE
    : { status: 'ok', layer, revision: doc.revision };
}

function conflict(scope: string, current: number, expected: number): never {
  throw new ControlPlaneRevisionConflictError(
    `policy layer ${scope} is at revision ${String(current)}, not ${String(expected)}`,
    String(current),
  );
}

/**
 * `PolicyStore` over `cpPolicyLayers` and `cpPolicyHistory` (03-interfaces
 * §2.5). Writes run through the control-plane transaction's session, so the
 * layer, its history row, the org head and the audit entry commit together.
 */
export class MongoPolicyStore
  implements PolicyStore, PolicyLayers, PolicyStorePurge
{
  async read(orgId: string, scope: string): Promise<VersionedLayer> {
    const found = (await this.readMany(orgId, [scope])).get(scope);
    if (found === undefined || found.status === 'unavailable') {
      throw new ControlPlaneUnavailableError(
        `the stored policy layer of ${scope} does not parse`,
      );
    }
    return { layer: found.layer, revision: found.revision };
  }

  async readMany(
    orgId: string,
    scopes: readonly string[],
  ): Promise<Map<string, LayerRead>> {
    const docs = await PolicyLayerModel.find({
      _id: { $in: scopes.map((scope) => idOf(orgId, scope)) },
    })
      .lean<PolicyLayerDocument[]>()
      .exec();
    const byScope = new Map(docs.map((doc) => [doc.scope, doc]));
    return new Map(
      scopes.map((scope) => [scope, layerRead(byScope.get(scope))]),
    );
  }

  async write(
    tx: StoreTx,
    scope: string,
    layer: PolicyLayer | null,
    expectedRevision: number,
  ): Promise<number> {
    if (layer !== null && readPolicyLayer(layer) === null) {
      throw new InvalidObject(`the policy layer for ${scope} does not parse`);
    }
    const session = sessionOf(tx);
    const _id = idOf(tx.orgId, scope);
    const revisionNow = async (): Promise<number> =>
      (
        await PolicyLayerModel.findOne({ _id }, { revision: 1 })
          .session(session ?? null)
          .lean<Pick<PolicyLayerDocument, 'revision'>>()
          .exec()
      )?.revision ?? 0;
    const current = await revisionNow();
    if (current !== expectedRevision)
      conflict(scope, current, expectedRevision);
    const revision = expectedRevision + 1;
    const body = layer === null ? null : JSON.stringify(layer);
    const at = new Date();
    await tx.enlist(async () => {
      if (expectedRevision === 0) {
        try {
          await PolicyLayerModel.create(
            [
              {
                _id,
                orgId: tx.orgId,
                scope,
                kind: scopeKindOf(scope),
                revision,
                body,
                updatedAt: at,
              },
            ],
            { session },
          );
        } catch (error) {
          if (isDuplicateKeyError(error))
            conflict(scope, await revisionNow(), 0);
          throw error;
        }
      } else {
        const moved = await PolicyLayerModel.updateOne(
          { _id, revision: expectedRevision },
          { $set: { revision, body, updatedAt: at } },
          { session },
        ).exec();
        if (moved.matchedCount === 0) {
          conflict(scope, await revisionNow(), expectedRevision);
        }
      }
      await PolicyHistoryModel.create(
        [
          {
            _id: `${_id}:${String(revision)}`,
            orgId: tx.orgId,
            scope,
            revision,
            body,
            at,
          },
        ],
        { session },
      );
    });
    await policyWritten(tx, scope, layer);
    return revision;
  }

  async history(
    orgId: string,
    scope: string,
    limit: number,
  ): Promise<PolicyRevision[]> {
    const docs = await PolicyHistoryModel.find({ orgId, scope })
      .sort({ revision: -1 })
      .limit(limit)
      .lean<PolicyHistoryDocument[]>()
      .exec();
    return docs.map((doc) => ({
      revision: doc.revision,
      // A history row that no longer parses is shown as no layer, never hidden.
      layer: parsed(doc.body) ?? null,
    }));
  }

  async scopesOf(orgId: string, kind: string): Promise<string[]> {
    const docs = await PolicyLayerModel.find(
      { orgId, kind, body: { $ne: null } },
      { scope: 1 },
    )
      .sort({ scope: 1 })
      .lean<Pick<PolicyLayerDocument, 'scope'>[]>()
      .exec();
    return docs.map((doc) => doc.scope);
  }

  orgSource(): Promise<PolicyOrgSource> {
    return Promise.resolve('store');
  }

  writeMany(
    tx: StoreTx,
    writes: readonly LayerWrite[],
  ): Promise<Map<string, number>> {
    return writeEach(this, tx, writes);
  }

  async *orgLayers(): AsyncIterable<{ orgId: string; layer: PolicyLayer }> {
    let after = '';
    for (;;) {
      const docs = await PolicyLayerModel.find({
        _id: { $gt: after },
        kind: 'org',
        body: { $ne: null },
      })
        .sort({ _id: 1 })
        .limit(ORG_SCAN_PAGE)
        .lean<PolicyLayerDocument[]>()
        .exec();
      for (const doc of docs) {
        const layer = parsed(doc.body);
        if (layer) yield { orgId: doc.orgId, layer };
        after = doc._id;
      }
      if (docs.length < ORG_SCAN_PAGE) return;
    }
  }

  async purge(tx: StoreTx, orgId: string): Promise<void> {
    const session = sessionOf(tx);
    await tx.enlist(async () => {
      await PolicyLayerModel.deleteMany({ orgId }, { session }).exec();
      await PolicyHistoryModel.deleteMany({ orgId }, { session }).exec();
    });
  }
}
