import type {
  WorkspaceKeyStore,
  WorkspaceRepository,
  WorkspaceWriterStore,
} from '../../../../modules/agent-harness/ports/workspace-store';
import { pickHarnessStore } from '../stores';
import {
  MongoWorkspaceKeyStore,
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from './mongo';
import {
  PgWorkspaceKeyStore,
  PgWorkspaceRepository,
  PgWorkspaceWriterStore,
} from './pg';

const NAME = 'agent-harness/workspace';

export const createWorkspaceRepository = (): WorkspaceRepository =>
  pickHarnessStore<WorkspaceRepository>(NAME, {
    mongo: () => new MongoWorkspaceRepository(),
    postgres: () => new PgWorkspaceRepository(),
  });

export const createWorkspaceWriterStore = (): WorkspaceWriterStore =>
  pickHarnessStore<WorkspaceWriterStore>(NAME, {
    mongo: () => new MongoWorkspaceWriterStore(),
    postgres: () => new PgWorkspaceWriterStore(),
  });

export const createWorkspaceKeyStore = (): WorkspaceKeyStore =>
  pickHarnessStore<WorkspaceKeyStore>(NAME, {
    mongo: () => new MongoWorkspaceKeyStore(),
    postgres: () => new PgWorkspaceKeyStore(),
  });
