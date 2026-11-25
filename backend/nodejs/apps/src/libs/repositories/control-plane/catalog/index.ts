import type { OrgCatalogRows } from '../../../../modules/control-plane/ports/catalog-store';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoOrgCatalogStore } from './mongo';
import { PgOrgCatalogStore } from './pg';

export const createOrgCatalogStore = (): OrgCatalogRows =>
  pickHarnessStore<OrgCatalogRows>('control-plane/catalog', {
    mongo: () => new MongoOrgCatalogStore(),
    postgres: () => new PgOrgCatalogStore(),
  });
