import { expect } from 'chai';
import { VISIBILITIES, WRITERS } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import { SessionEventModel } from '../../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';

/**
 * CPD0-25: the stored envelope's types are held to the generated envelope at
 * compile time (`contracts/envelope-parity.ts`); its Mongo model is held here.
 */
describe('agent-harness contracts: the stored envelope (CPD-0)', () => {
  it('the Mongo model accepts exactly the generated writers and visibilities', () => {
    const paths = SessionEventModel.schema.paths as Record<string, { enumValues?: string[] }>;
    expect(paths['writer']?.enumValues).to.deep.equal([...WRITERS]);
    expect(paths['visibility']?.enumValues).to.deep.equal([...VISIBILITIES]);
  });
});
