import { expect } from 'chai';
import {
  PRECISION_VERDICT_KEY,
  precisionWaitWarningFromStore,
} from '../../../../src/modules/tokens_manager/utils/precision-warning';

const store = (value: unknown) =>
  ({
    get: async (key: string) => (key === PRECISION_VERDICT_KEY ? value : null),
  }) as any;

describe('precisionWaitWarningFromStore', () => {
  it('names the value to set back for an index that holds fp32', async () => {
    const warning = await precisionWaitWarningFromStore(
      store(JSON.stringify({ mismatch: true, holds: ['fp32'] })),
    );
    expect(warning).to.include('EMBEDDING_SERVER_PRECISION=fp32');
    expect(warning).to.include('remove the setting');
  });

  it('names the value to set back for an index that holds int8', async () => {
    const warning = await precisionWaitWarningFromStore(store({ mismatch: true, holds: ['int8-w'] }));
    expect(warning).to.include('EMBEDDING_SERVER_PRECISION=int8');
    expect(warning).to.not.include('=fp32');
  });

  it('does not guess when the indexes hold more than one precision or none is known', async () => {
    const mixed = await precisionWaitWarningFromStore(store({ mismatch: true, holds: ['fp32', 'int8-w'] }));
    const unknown = await precisionWaitWarningFromStore(store({ mismatch: true }));
    for (const warning of [mixed, unknown]) {
      expect(warning).to.include('the value the index was built with');
    }
  });

  it('says where the cleanup is and what it deletes', async () => {
    const warning = await precisionWaitWarningFromStore(store({ mismatch: true, holds: ['fp32'] }));
    expect(warning).to.include('Workspace > Connectors').and.include('Clean up embeddings');
    expect(warning).to.include('deletes the whole search index').and.include('Reindex embeddings');
  });

  it('says nothing for a match, a missing key or an expired one', async () => {
    expect(await precisionWaitWarningFromStore(store(JSON.stringify({ mismatch: false })))).to.equal(null);
    expect(await precisionWaitWarningFromStore(store(null))).to.equal(null);
  });
});
