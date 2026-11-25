import type {
  DeploymentRevision,
  HeadRevisions,
  Heads,
} from '../../../../modules/control-plane/ports/head-revisions';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoDeploymentRevision, MongoHeadRevisions } from './mongo';
import { PgDeploymentRevision, PgHeadRevisions } from './pg';

/** Head reads; `heads(orgId, session)` reads inside the caller's transaction (the store's own session type). */
export interface SessionHeadRevisions extends HeadRevisions {
  heads(orgId: string, session?: unknown): Promise<Heads>;
}

export const createHeadRevisions = (): SessionHeadRevisions =>
  pickHarnessStore<SessionHeadRevisions>('control-plane/head-revisions', {
    mongo: () => new MongoHeadRevisions(),
    postgres: () => new PgHeadRevisions(),
  });

export const createDeploymentRevision = (): DeploymentRevision =>
  pickHarnessStore<DeploymentRevision>('control-plane/head-revisions', {
    mongo: () => new MongoDeploymentRevision(),
    postgres: () => new PgDeploymentRevision(),
  });
