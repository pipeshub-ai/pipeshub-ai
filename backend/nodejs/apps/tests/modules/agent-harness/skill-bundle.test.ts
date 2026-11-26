/**
 * P7.1 SD-2, SD-5: Node's skill digests equal Python's on the shared vectors
 * (contracts/harness/vectors/skill-bundles.json). AD-2, AD-3: admission pins
 * the content digest Python reports, and the old name-and-version pin without it.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { agentRunDefinitionSchema } from '../../../src/modules/agent-harness/domain/agent-definition';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import { resolveRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  legacySkillPinDigest,
  skillBundleDigest,
} from '../../../src/modules/agent-harness/domain/skill-bundle';
import { agentDefinition } from './support/agent-definition';

interface Vectors {
  bundles: Array<{
    description: string;
    files: Record<string, string>;
    digest: string;
  }>;
  legacyPins: Array<{ name: string; version: string | null; digest: string }>;
}

const vectors = JSON.parse(
  readFileSync(
    path.resolve(
      __dirname,
      '../../../../../../contracts/harness/vectors/skill-bundles.json',
    ),
    'utf8',
  ),
) as Vectors;

const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);
const CONTENT_DIGEST = `sha256:${'ab'.repeat(32)}`;

const run = (): HarnessRun => ({
  runId: 'run-1',
  orgId: 'org-1',
  sessionId: 'session-1',
  actorId: 'user-1',
  agentKey: 'agent-1',
  userMessageKey: 'message-1',
  idempotencyKey: 'idem-1',
  payloadHash: 'hash',
  harnessMajor: 2,
  profile: 'dispatched',
  status: 'queued',
  outcome: null,
  revision: 0,
  fence: 0,
  attemptId: null,
  workerId: null,
  leaseExpiresAt: null,
  absoluteDeadlineAt: Date.UTC(2026, 9, 1, 12),
  checkpoint: null,
  input: {
    query: 'q',
    recordIds: [],
    filters: {},
    attachments: [],
    model: { chatMode: 'auto' },
  },
  createdAt: Date.UTC(2026, 9, 1, 11),
  updatedAt: Date.UTC(2026, 9, 1, 11),
});

const pinnedSkills = (
  skills: Array<{
    name: string;
    version: string | null;
    bundleDigest?: string | null;
  }>,
): unknown =>
  resolveRunSpec(
    { run: run(), agentDefinition: agentDefinition({ skills }), via: 'pat' },
    DURABLE,
  ).extensions?.['x.pipeshub']?.['skills'];

describe('skill bundle digests (P7.1)', () => {
  for (const vector of vectors.bundles) {
    it(`SD-2 equals Python: ${vector.description}`, () => {
      const files = new Map(
        Object.entries(vector.files).map(([p, b64]) => [
          p,
          Buffer.from(b64, 'base64'),
        ]),
      );
      expect(skillBundleDigest(files)).to.equal(vector.digest);
    });
  }

  for (const vector of vectors.legacyPins) {
    it(`SD-5 legacy pin ${vector.name}@${vector.version}`, () => {
      expect(legacySkillPinDigest(vector.name, vector.version)).to.equal(
        vector.digest,
      );
    });
  }

  it('AD-2 pins the content digest the run definition carries', () => {
    expect(
      pinnedSkills([
        { name: 'xlsx', version: '3', bundleDigest: CONTENT_DIGEST },
      ]),
    ).to.deep.equal([{ name: 'xlsx', bundleDigest: CONTENT_DIGEST }]);
  });

  it('AD-3 pins digest(name, version) when an older authority sends none', () => {
    expect(
      pinnedSkills([
        { name: 'xlsx', version: '3' },
        { name: 'docx', version: null, bundleDigest: null },
      ]),
    ).to.deep.equal([
      { name: 'docx', bundleDigest: legacySkillPinDigest('docx', null) },
      { name: 'xlsx', bundleDigest: legacySkillPinDigest('xlsx', '3') },
    ]);
  });

  it("FIX-SKILLS pins the assistant's offered packs, so a default chat's engine mounts them", () => {
    const pdf = `sha256:${'cd'.repeat(32)}`;
    const spec = resolveRunSpec(
      {
        run: { ...run(), agentKey: ASSISTANT_AGENT_DEFINITION_KEY },
        agentDefinition: agentDefinition({
          agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
          skills: [
            { name: 'pdf', version: null, bundleDigest: pdf },
            { name: 'docx', version: null, bundleDigest: CONTENT_DIGEST },
          ],
        }),
        via: 'pat',
      },
      DURABLE,
    );
    expect(spec.extensions?.['x.pipeshub']?.['skills']).to.deep.equal([
      { name: 'docx', bundleDigest: CONTENT_DIGEST },
      { name: 'pdf', bundleDigest: pdf },
    ]);
  });

  it('keeps a well-formed digest from Python and refuses a malformed one', () => {
    const wire = (bundleDigest: unknown) => ({
      ...agentDefinition(),
      skills: [{ name: 'xlsx', version: '3', bundleDigest }],
    });
    expect(
      agentRunDefinitionSchema.parse(wire(CONTENT_DIGEST)).skills[0]
        ?.bundleDigest,
    ).to.equal(CONTENT_DIGEST);
    expect(agentRunDefinitionSchema.safeParse(wire('latest')).success).to.equal(
      false,
    );
  });
});
