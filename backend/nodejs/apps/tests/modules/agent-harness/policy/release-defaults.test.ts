/**
 * CPD3-50..CPD3-54 (POL-24, R2 F10): every default the release ships is
 * `contracts/harness/core/policy/release.json`, read through the generated
 * `RELEASE_DEFAULTS`; no module keeps a copy of it.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DEFAULT_OFFERING } from '../../../../src/modules/agent-harness/application/model-access-service';
import { pinnedEngineProfileSchema } from '../../../../src/modules/agent-harness/contracts/generated/core/engine-profile';
import {
  RELEASE_DEFAULTS,
  RELEASE_ENGINES,
} from '../../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { MAX_CHILDREN_PER_RUN } from '../../../../src/modules/agent-harness/domain/child-runs';
import { RELEASE_ENGINE_PROFILES } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import { MAX_MEMORY_CHARS } from '../../../../src/modules/agent-harness/domain/memory';
import { DEEP_MAX_CHILDREN, modeMaxChildren } from '../../../../src/modules/agent-harness/domain/rollout';
import { editMemorySchema } from '../../../../src/modules/agent-harness/transport/memory-routes';
import { admissionBodySchema } from '../../../../src/modules/agent-harness/transport/schemas';

const REPO = join(__dirname, '../../../../../../..');
const RELEASE = JSON.parse(readFileSync(join(REPO, 'contracts/harness/core/policy/release.json'), 'utf8')) as typeof RELEASE_DEFAULTS;
const ALLOWLIST = JSON.parse(readFileSync(join(REPO, 'backend/python/harness_engines/allowlist.json'), 'utf8')) as {
  engines: Array<{ kind: string; version: string; binaryDigest: string }>;
  defaultProfile?: unknown;
};

describe('CPD-3 release defaults are defined once', () => {
  it('RELEASE_DEFAULTS is release.json', () => {
    expect(RELEASE_DEFAULTS).to.deep.equal(RELEASE);
  });

  it('CPD3-50 a deep run and an agent definition share the one children bound', () => {
    expect(DEEP_MAX_CHILDREN).to.equal(RELEASE.children.maxPerRun);
    expect(MAX_CHILDREN_PER_RUN).to.equal(RELEASE.children.maxPerRun);
    expect(modeMaxChildren('deep')).to.equal(RELEASE.children.maxPerRun);
  });

  it('CPD3-51 a memory of the release length is accepted by the route and one more is refused', () => {
    expect(MAX_MEMORY_CHARS).to.equal(RELEASE.memory.maxChars);
    const edit = (content: string) =>
      editMemorySchema.safeParse({ params: { memoryId: 'm1' }, body: { content } }).success;
    expect(edit('x'.repeat(RELEASE.memory.maxChars))).to.equal(true);
    expect(edit('x'.repeat(RELEASE.memory.maxChars + 1))).to.equal(false);
  });

  it('CPD3-52 the offering default is the release one', () => {
    expect(DEFAULT_OFFERING).to.equal(RELEASE.models.offering);
  });

  it('CPD3-53 an admission that names no deadline gets the release one', () => {
    const body = admissionBodySchema.parse({ query: 'hello', idempotencyKey: 'k1' });
    expect(body.deadlineMs).to.equal(RELEASE.runs.deadlineMs);
  });

  it('CPD3-54 the release engine profiles are the allowlist engines with the release profile', () => {
    expect(ALLOWLIST.defaultProfile).to.equal(undefined);
    expect(RELEASE_ENGINES).to.deep.equal(
      ALLOWLIST.engines.map(({ kind, version, binaryDigest }) => ({ kind, version, binaryDigest })),
    );
    expect(RELEASE_ENGINE_PROFILES).to.deep.equal(
      RELEASE_ENGINES.map((engine) => ({ engine, ...RELEASE.engineProfile })),
    );
    for (const profile of RELEASE_ENGINE_PROFILES) pinnedEngineProfileSchema.parse(profile);
  });
});
