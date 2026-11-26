/**
 * WP-23 (OWD-12): a worker is handed only runs of its own region, and names
 * its region on every claim. Which region a process is in, and where an org's
 * runs may be placed, are extension points: this build runs in one region,
 * `default`, and places every run here.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  deploymentRegion,
  isRegion,
  resetRegionSource,
  UNSET_REGION,
  useRegionSource,
  workerMayRun,
} from '../../../src/modules/agent-harness/domain/region';
import {
  claimingWorker,
  missingFor,
  placesRun,
  REGION_AWARE_FEATURE,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import { claimNextRunSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';

/** A worker older than regions: it advertises no `region_aware` and names no region. */
const BEFORE_REGIONS = {
  ...CURRENT_WORKER_CAPABILITIES,
  features: CURRENT_WORKER_CAPABILITIES.features.filter((f) => f !== REGION_AWARE_FEATURE),
};

describe('WP-23 regions (OWD-12)', () => {
  afterEach(resetRegionSource);

  it('RES-1: a process runs in `default` unless an extension point names its region', () => {
    expect(deploymentRegion()).to.equal(UNSET_REGION);
    expect(UNSET_REGION).to.equal('default');
    useRegionSource(() => 'eu-west');
    expect(deploymentRegion()).to.equal('eu-west');
    resetRegionSource();
    expect(deploymentRegion()).to.equal('default');
    for (const bad of ['eu west', '-eu', 'eu_', 'EU', 'x'.repeat(65)]) expect(isRegion(bad), bad).to.equal(false);
    expect(isRegion('eu-west-1')).to.equal(true);
  });

  it("RES-3: a worker is handed a run of its own region only; a run from before regions is the Node's region's", () => {
    const here = 'eu-west';
    const euWorker = claimingWorker(CURRENT_WORKER_CAPABILITIES, { worker: 'eu-west', here });
    const usWorker = claimingWorker(CURRENT_WORKER_CAPABILITIES, { worker: 'us-east', here });
    const olderWorker = claimingWorker(BEFORE_REGIONS, { worker: undefined, here });
    const run = (region?: string) => ({ harnessMajor: 2 as const, ...(region !== undefined ? { region } : {}) });
    expect(missingFor(run('eu-west'), euWorker)).to.deep.equal([]);
    expect(missingFor(run('eu-west'), usWorker)).to.deep.equal(['region']);
    expect(missingFor(run(), euWorker)).to.deep.equal([]);
    expect(missingFor(run(), usWorker)).to.deep.equal(['region']);
    expect(missingFor(run('eu-west'), olderWorker)).to.deep.equal([]);
    expect(missingFor({ harnessMajor: 1 as never, region: 'us-east' }, euWorker)).to.deep.equal(['region']);
    expect(placesRun('us-east', claimingWorker(CURRENT_WORKER_CAPABILITIES))).to.equal(true);
    expect(workerMayRun(undefined, 'eu-west', 'eu-west')).to.equal(true);
    expect(workerMayRun('us-east', 'eu-west', 'eu-west')).to.equal(false);
  });

  it('WP88-FU-2: a region-aware worker with no region is no region\'s; only a worker older than regions is Node\'s own', () => {
    const run = (region?: string) => ({ harnessMajor: 2 as const, ...(region !== undefined ? { region } : {}) });
    expect(CURRENT_WORKER_CAPABILITIES.features).to.include(REGION_AWARE_FEATURE);
    // A regioned Node: the unset worker places no run of the region, nor one from before regions.
    const unset = claimingWorker(CURRENT_WORKER_CAPABILITIES, { worker: undefined, here: 'eu-west' });
    expect(unset.placement).to.deep.equal({ region: UNSET_REGION, servesUnregioned: false });
    expect(missingFor(run('eu-west'), unset)).to.deep.equal(['region']);
    expect(missingFor(run(), unset)).to.deep.equal(['region']);
    // An unregioned Node (its region `default`): the unset worker places everything it holds.
    const single = claimingWorker(CURRENT_WORKER_CAPABILITIES, { worker: undefined, here: UNSET_REGION });
    expect(missingFor(run(), single)).to.deep.equal([]);
    expect(missingFor(run(UNSET_REGION), single)).to.deep.equal([]);
    // A worker older than regions keeps today's reading: the deployment's, in this Node's region (UX2).
    const older = claimingWorker(BEFORE_REGIONS, { worker: undefined, here: 'eu-west' });
    expect(older.placement).to.deep.equal({ region: 'eu-west', servesUnregioned: true });
    expect(missingFor(run('eu-west'), older)).to.deep.equal([]);
    // A worker that names its region is placed by it, aware or not.
    expect(claimingWorker(CURRENT_WORKER_CAPABILITIES, { worker: 'eu-west', here: 'eu-west' }).placement).to.deep.equal({
      region: 'eu-west',
      servesUnregioned: true,
    });
  });

  it('RES-4: the claim names the worker region; an older worker that does not is accepted', () => {
    const body = { workerId: 'w', leaseDurationMs: 1000, role: 'dispatched', workerCapabilities: CURRENT_WORKER_CAPABILITIES };
    expect(claimNextRunSchema.shape.body.parse({ ...body, workerRegion: 'eu-west' }).workerRegion).to.equal('eu-west');
    expect(claimNextRunSchema.shape.body.parse(body).workerRegion).to.equal(undefined);
    expect(() => claimNextRunSchema.shape.body.parse({ ...body, workerRegion: 'EU WEST' })).to.throw();
  });
});
