/**
 * C2.07 / C2.20 (SL-14): a run pins what it was admitted under. Its claim pins `x.pipeshub.release` (the
 * `/pipeshub` contract version, one catalog digest, the policy revision of its control-plane pin) and an engine
 * run its image digest (`pins.sandboxImage`, as the sandbox broker reported it). The pin is part of the spec, so
 * a re-claim (a resume) holds the same one; a worker that does not read it never claims a run that carries it.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { parseEngineImages } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { HttpEngineAvailability } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { engineImageDigests } from '../../../src/modules/agent-harness/application/engine-image-pin';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import type { PipeshubRunSpecExtension } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { childFeatures, childGrants, childRunSpec } from '../../../src/modules/agent-harness/domain/child-runs';
import {
  engineExecutor,
  engineRef,
  RELEASE_ENGINE_PROFILES,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import { PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import {
  catalogDigest,
  PIPESHUB_CONTRACT_VERSION,
  readPinnedSpec,
  RELEASE_PIN_FEATURE,
  runSpecResolver,
  profileExecutor,
  SpecSubject,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { missingFor } from '../../../src/modules/agent-harness/domain/worker-compat';
import type { ResolvedRunConfig } from '../../../src/modules/control-plane';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { runFailure } from '../../../src/modules/agent-harness/projection/transcript';
import { readJournal } from '../../contracts/support/durable';
import { goldenSpecSubject } from './support/golden-run-spec';
import { currentWorker } from './support/worker-capabilities';

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const PIN = runSpecResolver(profileExecutor);
const PROFILE = RELEASE_ENGINE_PROFILES[0]!;
const IMAGE = `sha256:${'1'.repeat(64)}`;
const OTHER_IMAGE = `sha256:${'2'.repeat(64)}`;
const IMAGES = new Map([[engineRef(PROFILE.engine), IMAGE]]);

const VERSION_FILE = join(__dirname, '../../../../../python/harness_engines/pipeshub/VERSION');

const subject = (features: string[], extra: Partial<SpecSubject> = {}): SpecSubject => {
  const golden = goldenSpecSubject();
  return {
    ...golden,
    run: { ...golden.run, requires: { foldVersion: 1, features: features as never } },
    ...extra,
  };
};

const pipeshub = (spec: RunSpec): PipeshubRunSpecExtension =>
  spec.extensions?.['x.pipeshub'] as PipeshubRunSpecExtension;

/** A control-plane pin holding the release's config and an admitted request's deadline, at head revision 7. */
const resolvedPin = (): ResolvedRunConfig => {
  const config = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'request', values: { schemaVersion: 1, scope: { kind: 'request' }, budgets: { activeTimeS: 600 } } },
  ]).config;
  if (config === null) return expect.fail('the release config does not resolve');
  return { headRevision: 7, digest: `sha256:${'7'.repeat(64)}`, effectiveConfig: config } as unknown as ResolvedRunConfig;
};

describe('C2.07 / C2.20: the release pin (SL-14)', () => {
  describe('the spec a claim pins', () => {
    it('SL-14: a run that requires release_pin pins the /pipeshub contract and its catalog digest', () => {
      const { spec } = PIN(subject([RELEASE_PIN_FEATURE]), DURABLE);
      const release = pipeshub(spec).release!;
      expect(release.pipeshubContract).to.equal(PIPESHUB_CONTRACT_VERSION);
      // The contract Node pins is the one the engine images declare (one source: harness_engines/pipeshub/VERSION).
      expect(PIPESHUB_CONTRACT_VERSION).to.equal(Number(readFileSync(VERSION_FILE, 'utf8').trim()));
      expect(release.catalogDigest).to.equal(
        canonicalDigest({ tools: spec.grants.tools, mcp: pipeshub(spec).mcp, skills: pipeshub(spec).skills }),
      );
      expect(release.catalogDigest).to.equal(catalogDigest(spec.grants.tools, pipeshub(spec)));
      // No control-plane pin (CONTROL_PLANE_PIN=off): no policy revision to name.
      expect(release).to.not.have.property('policyRevision');
      // A loop run has no image to pin.
      expect(spec).to.not.have.property('pins');
    });

    it('SL-14: the policy revision and digest are those of the run\'s control-plane pin', () => {
      const pin = resolvedPin();
      const { spec } = PIN(subject([RELEASE_PIN_FEATURE], { resolved: pin }), DURABLE);
      expect(pipeshub(spec).release).to.include({ policyRevision: 7, policyDigest: pin.digest });
    });

    it('SL-14: an engine run pins the image digest the broker reported for its engine', () => {
      const { spec } = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE, engineImages: IMAGES }), DURABLE);
      expect(spec.executor?.kind).to.equal('engine');
      expect(spec.pins).to.deep.equal({ sandboxImage: IMAGE });
      expect(pipeshub(spec).release?.pipeshubContract).to.equal(PIPESHUB_CONTRACT_VERSION);
    });

    it('an engine run whose image the broker did not report pins none (its worker uses what the broker has)', () => {
      const unknown = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE, engineImages: new Map() }), DURABLE);
      expect(unknown.spec).to.not.have.property('pins');
      const unasked = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE }), DURABLE);
      expect(unasked.spec).to.not.have.property('pins');
    });

    it('SL-14: the same admission pins the same spec, and a re-claim reads it back unchanged (resume)', () => {
      const first = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE, engineImages: IMAGES }), DURABLE);
      const again = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE, engineImages: IMAGES }), DURABLE);
      expect(again.specDigest).to.equal(first.specDigest);
      const stored = JSON.parse(JSON.stringify(first.spec)) as unknown;
      const read = readPinnedSpec(stored);
      expect(read.pins).to.deep.equal({ sandboxImage: IMAGE });
      expect(pipeshub(read).release).to.deep.equal(pipeshub(first.spec).release);
      expect(canonicalDigest(read)).to.equal(first.specDigest);
    });

    it('a different image is a different pin: the digest names exactly one image', () => {
      const one = PIN(subject([RELEASE_PIN_FEATURE], { engineProfile: PROFILE, engineImages: IMAGES }), DURABLE);
      const other = PIN(
        subject([RELEASE_PIN_FEATURE], {
          engineProfile: PROFILE,
          engineImages: new Map([[engineRef(PROFILE.engine), OTHER_IMAGE]]),
        }),
        DURABLE,
      );
      expect(other.specDigest).to.not.equal(one.specDigest);
    });

    it('old workers: a run admitted without release_pin carries neither pin, and one that needs it waits', () => {
      const { spec } = PIN(subject([], { engineProfile: PROFILE, engineImages: IMAGES }), DURABLE);
      expect(pipeshub(spec)).to.not.have.property('release');
      expect(spec).to.not.have.property('pins');
      const needs = subject([RELEASE_PIN_FEATURE]).run;
      expect(missingFor(needs, currentWorker({ features: ['chat'] }))).to.include(`feature:${RELEASE_PIN_FEATURE}`);
      expect(missingFor(needs, currentWorker())).to.deep.equal([]);
    });
  });

  describe('a child', () => {
    const parent = () => {
      const pinned = PIN(subject([RELEASE_PIN_FEATURE], { resolved: resolvedPin() }), DURABLE);
      return { ...pinned, spec: { ...pinned.spec, budgets: { ...pinned.spec.budgets, maxChildren: 4 } } };
    };

    it('SL-14: an engine child pins its own catalog, its parent\'s contract and policy, and its image', () => {
      const pinned = parent();
      const decision = childGrants(pinned.spec, { tools: ['jira@inst-42/search_issues'], maxTurns: 5 }, 1);
      if (decision.grants === null) return expect.fail('expected a grant');
      const child = childRunSpec(
        pinned,
        decision.grants,
        undefined,
        undefined,
        engineExecutor(PROFILE),
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        IMAGE,
      );
      const release = pipeshub(child.spec).release!;
      expect(release.catalogDigest).to.equal(catalogDigest(child.spec.grants.tools, pipeshub(child.spec)));
      expect(release.catalogDigest).to.not.equal(pipeshub(pinned.spec).release!.catalogDigest);
      expect(release).to.include({ pipeshubContract: PIPESHUB_CONTRACT_VERSION, policyRevision: 7 });
      expect(child.spec.pins).to.deep.equal({ sandboxImage: IMAGE });
      expect(childFeatures(child)).to.include(RELEASE_PIN_FEATURE);
    });

    it('a loop child pins no image, and a child of an unpinned parent pins nothing', () => {
      const pinned = parent();
      const decision = childGrants(pinned.spec, { maxTurns: 5 }, 1);
      if (decision.grants === null) return expect.fail('expected a grant');
      const args = [undefined, undefined, undefined, undefined, undefined, undefined] as const;
      const loop = childRunSpec(pinned, decision.grants, undefined, undefined, undefined, ...args, IMAGE);
      expect(loop.spec).to.not.have.property('pins');
      const bare = PIN(subject([]), DURABLE);
      const unpinned = childRunSpec(
        { ...bare, spec: { ...bare.spec, budgets: { ...bare.spec.budgets, maxChildren: 4 } } },
        decision.grants,
        undefined,
        undefined,
        engineExecutor(PROFILE),
        ...args,
        IMAGE,
      );
      expect(unpinned.spec).to.not.have.property('pins');
      expect(pipeshub(unpinned.spec)).to.not.have.property('release');
      expect(childFeatures(unpinned)).to.not.include(RELEASE_PIN_FEATURE);
    });
  });

  describe('the broker\'s image digests', () => {
    it('reads kind@version=sha256 entries and drops malformed ones, never pinning them', () => {
      const parsed = parseEngineImages(
        `opencode@1.18.34=${IMAGE},codex@0.160.0=sha256:short,bad,claude_code@2.1.294=${OTHER_IMAGE}`,
      );
      expect([...(parsed ?? new Map())]).to.deep.equal([
        ['opencode@1.18.34', IMAGE],
        ['claude_code@2.1.294', OTHER_IMAGE],
      ]);
      expect(parseEngineImages(undefined)).to.equal(null);
    });

    it('a claim asks the broker afresh each time: an image rebuilt a moment ago is the one a new run pins', async () => {
      let answer = IMAGE;
      let asked = 0;
      const availability = new HttpEngineAvailability(
        async () => 'http://broker:8090',
        async () => 'credential',
        (async () => {
          asked += 1;
          return new Response(JSON.stringify({ ok: true, modes: ['docker'], detail: { engineImages: `opencode@1.18.34=${answer}` } }));
        }) as typeof fetch,
        { ttlMs: 0 },
      );
      expect((await availability.imageDigests())?.get('opencode@1.18.34')).to.equal(IMAGE);
      answer = OTHER_IMAGE;
      expect((await availability.imageDigests())?.get('opencode@1.18.34')).to.equal(OTHER_IMAGE);
      expect(asked).to.equal(2);
    });

    it('a broker that cannot answer never holds the run back: it pins no image and the gap is logged', async () => {
      const warned: string[] = [];
      const logger = { debug() {}, info() {}, warn: (event: string) => warned.push(event), error() {} };
      const digests = await engineImageDigests(
        { imageDigests: () => Promise.reject(new Error('down')) },
        logger as never,
        { orgId: 'org-1', runId: 'run-1' },
      );
      expect(digests).to.equal(null);
      expect(warned).to.deep.equal(['run.image_unpinned']);
      expect(await engineImageDigests(null, logger as never, {})).to.equal(null);
    });
  });

  describe('what the person reads', () => {
    const base = readJournal('agent_quick_tool_call');
    const claimed = base.events.filter((e) => e.type === 'run.admitted' || e.type === 'run.claimed');
    const last = claimed[claimed.length - 1] as JournalEvent;
    const failedWith = (errorType: string) =>
      foldTimeline(
        [...claimed, { ...last, seq: last.seq + 1, type: 'run.failed', writer: 'node', critical: true, data: { errorType } }],
        base.runId,
      );

    for (const code of ['engine_image_changed', 'image_digest_mismatch']) {
      it(`${code}: the engine was updated under the run; sending again runs on the updated engine`, () => {
        const failure = runFailure(failedWith(code));
        expect(failure.code).to.equal(code);
        expect(failure.message).to.match(/was updated while this task was waiting/);
        expect(failure.message).to.match(/Send your message again/);
      });
    }
  });
});
