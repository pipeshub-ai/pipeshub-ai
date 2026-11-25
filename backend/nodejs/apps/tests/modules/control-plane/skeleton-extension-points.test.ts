/** CP-SKEL: the extension-point conformance contract (EP) on the in-memory domain module. */
import { describeDomainModuleContract } from './support/extension-points-contract';
import { inMemoryDomainModule } from './support/in-memory-extension-points';
import { catalogItem } from './support/catalog-fixtures';
import { anInstallation } from './support/cp-objects';

describeDomainModuleContract('in-memory demo module', inMemoryDomainModule(), {
  item: catalogItem({ kind: 'demo' }),
  installation: anInstallation({ kind: 'demo', item: 'demo:org/server@1' }),
  validConfig: { url: 'https://mcp.example.test/sse' },
  invalidConfigs: [{}, { url: 'http://plain.example.test' }, null, { url: 42 }],
});
