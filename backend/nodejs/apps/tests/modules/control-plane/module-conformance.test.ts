/** CPD2-53, CPD2-54: the production domain modules and their release catalog. */
import { expect } from 'chai';
import { buildReleaseCatalog } from '../../../src/modules/control-plane/application/release-catalog';
import {
  CONTROL_PLANE_MODULES,
  createExtensionRegistry,
} from '../../../src/modules/control-plane/composition';
import { describeModuleConformance } from './support/module-conformance';

describe('control-plane production modules (CPD2-54)', () => {
  it('registers exactly the toolset, mcp, skill and engine kinds', () => {
    expect(createExtensionRegistry().kinds()).to.deep.equal([
      'toolset',
      'mcp',
      'skill',
      'engine',
    ]);
  });

  it('builds one release catalog from them', () => {
    const items = buildReleaseCatalog(createExtensionRegistry());
    const kinds = new Set(items.map((item) => item.kind));
    expect([...kinds]).to.deep.equal(['toolset', 'mcp', 'skill', 'engine']);
  });
});

for (const module of CONTROL_PLANE_MODULES) describeModuleConformance(module);
