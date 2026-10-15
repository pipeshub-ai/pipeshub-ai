import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { noopEditionBoot } from '../../../src/libs/edition/edition-boot';
import { editionBoot } from '../../../src/config';

describe('noopEditionBoot', () => {
  const logger: any = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };

  it('is the boot object this build uses', () => {
    expect(editionBoot).to.equal(noopEditionBoot);
  });

  it('adds no broker topics', () => {
    expect(noopEditionBoot.topics).to.deep.equal([]);
  });

  it('adds no content security policy sources', () => {
    expect(noopEditionBoot.cspSources).to.deep.equal({ connectSrc: [], scriptSrc: [], frameSrc: [] });
  });

  it('mounts nothing and starts nothing', async () => {
    const app: any = { use: sinon.spy(), get: sinon.spy(), post: sinon.spy() };

    await noopEditionBoot.initialize({} as any, {} as any);
    noopEditionBoot.mountRawRoutes(app);
    noopEditionBoot.mountRoutes(app, {});
    noopEditionBoot.startWorkers(logger);
    await noopEditionBoot.afterListen(logger, {});
    await noopEditionBoot.stop(logger, {});
    await noopEditionBoot.dispose();

    expect(app.use.called || app.get.called || app.post.called).to.be.false;
  });
});
