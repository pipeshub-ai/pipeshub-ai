import { CompositeCatalogStore } from '../../../src/modules/control-plane/adapters/composite-catalog-store';
import { InMemoryOrgCatalogStore } from '../../../src/modules/control-plane/adapters/in-memory-catalog-store';
import { ReleaseCatalogStore } from '../../../src/modules/control-plane/adapters/release-catalog-store';
import { describeCatalogStoreContract } from './support/catalog-store-contract';

describeCatalogStoreContract(
  'release + in memory',
  async (release) =>
    new CompositeCatalogStore(
      new ReleaseCatalogStore(release),
      new InMemoryOrgCatalogStore(),
    ),
);
