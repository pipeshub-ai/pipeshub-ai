import type { InstallationStore } from '../../../../modules/control-plane/ports/installation-store';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoInstallationStore } from './mongo';
import { PgInstallationStore } from './pg';

export const createInstallationStore = (): InstallationStore =>
  pickHarnessStore<InstallationStore>('control-plane/installations', {
    mongo: () => new MongoInstallationStore(),
    postgres: () => new PgInstallationStore(),
  });
