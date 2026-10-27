import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';
import * as platformSettings from '../../../../src/modules/configuration_manager/utils/util';
import {
  PROJECTS_DISABLED_MESSAGE,
  ProjectsFeature,
  requireProjectsEnabled,
} from '../../../../src/modules/projects/utils/projects-feature';

function stubStore(connected: boolean): void {
  sinon.stub(ProjectsFeature, 'settingsStore').returns({ isConnected: () => connected } as any);
}

function stubFlags(featureFlags: Record<string, boolean>): sinon.SinonStub {
  return sinon
    .stub(platformSettings, 'getPlatformSettingsFromStore')
    .resolves({ fileUploadMaxSizeBytes: 1, featureFlags });
}

describe('projects/utils/projects-feature', () => {
  beforeEach(() => ProjectsFeature.resetCache());
  afterEach(() => {
    sinon.restore();
    ProjectsFeature.resetCache();
  });

  it('is on when the Labs flag is on', async () => {
    stubStore(true);
    stubFlags({ ENABLE_PROJECTS: true });

    expect(await ProjectsFeature.isEnabled()).to.equal(true);
  });

  it('is off when the Labs flag is off, and assertEnabled refuses with a message naming Labs', async () => {
    stubStore(true);
    stubFlags({ ENABLE_PROJECTS: false });

    expect(await ProjectsFeature.isEnabled()).to.equal(false);
    try {
      await ProjectsFeature.assertEnabled();
      expect.fail('expected ForbiddenError');
    } catch (error) {
      expect(error).to.be.instanceOf(ForbiddenError);
      expect((error as Error).message).to.equal(PROJECTS_DISABLED_MESSAGE);
    }
  });

  it('leaves Projects on, without reading, while the settings store is not connected', async () => {
    stubStore(false);
    const read = stubFlags({ ENABLE_PROJECTS: false });

    expect(await ProjectsFeature.isEnabled()).to.equal(true);
    expect(read.called).to.equal(false);
  });

  it('leaves Projects on when the settings read fails', async () => {
    stubStore(true);
    sinon.stub(platformSettings, 'getPlatformSettingsFromStore').rejects(new Error('decrypt failed'));

    expect(await ProjectsFeature.isEnabled()).to.equal(true);
  });

  it('reads the settings once for calls close together', async () => {
    stubStore(true);
    const read = stubFlags({ ENABLE_PROJECTS: true });

    await ProjectsFeature.isEnabled();
    await ProjectsFeature.isEnabled();

    expect(read.calledOnce).to.equal(true);
  });

  it('the route guard passes the refusal to next while the flag is off', async () => {
    stubStore(true);
    stubFlags({ ENABLE_PROJECTS: false });
    const next = sinon.spy();

    await requireProjectsEnabled({} as any, {} as any, next);

    expect(next.calledOnce).to.equal(true);
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);
  });

  it('the route guard lets the request through while the flag is on', async () => {
    stubStore(true);
    stubFlags({ ENABLE_PROJECTS: true });
    const next = sinon.spy();

    await requireProjectsEnabled({} as any, {} as any, next);

    expect(next.calledOnceWithExactly()).to.equal(true);
  });
});
