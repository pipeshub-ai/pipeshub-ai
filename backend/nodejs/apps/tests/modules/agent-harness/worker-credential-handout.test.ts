import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { HarnessAuthority } from '../../../src/modules/agent-harness/application/harness-authority';
import { WorkerCredentialAuthority } from '../../../src/modules/agent-harness/application/worker-credential-authority';
import { WorkerCredentialSet } from '../../../src/modules/agent-harness/domain/authority';

describe('agent-harness worker credential handed to an inline claimer', () => {
  const setup = () => {
    let n = 0;
    const workers = new WorkerCredentialAuthority(
      new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      { now: () => 1_000 },
      () => `phw_${String((n += 1))}`,
    );
    const tokens = { provision: sinon.stub().resolves(), rotate: sinon.stub().resolves() };
    return { workers, tokens, authority: new HarnessAuthority(workers, tokens as never, {} as never) };
  };

  it('no credential until provisioned; then the current one, and the rotated one after a rotation', async () => {
    const { authority } = setup();
    expect(await authority.workerCredential()).to.equal(null);
    await authority.provisionWorkerCredential(null);
    expect(await authority.workerCredential()).to.equal('phw_1');
    await authority.rotateWorkerCredential();
    expect(await authority.workerCredential()).to.equal('phw_2');
    await authority.verifyWorker('phw_2');
  });

  it('provisioning the worker credential alone leaves the attempt-token keyring untouched', async () => {
    const { authority, tokens } = setup();
    await authority.provisionWorkerCredential('rotate-1');
    expect(tokens.provision.called).to.equal(false);
    await authority.provision('rotate-1');
    expect(tokens.provision.calledOnceWithExactly('rotate-1')).to.equal(true);
  });
});
