/**
 * CPD2-1..CPD2-6: the catalog digest scheme, on the vectors Python generated
 * (contracts/control-plane/vectors/digests.json).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  descriptorSetDigest,
  engineDigest,
  skillDescriptorDigest,
  toolDescriptorDigest,
} from './support/digests';
import {
  formatItemRef,
  parseItemRef,
} from '../../../src/modules/control-plane/domain/item-ref';
import {
  DuplicateDescriptorName,
  InvalidItemRef,
} from '../../../src/modules/control-plane/domain/catalog-errors';
import { skillBundleDigest } from '../../../src/modules/agent-harness/domain/skill-bundle';
import { CONTROL_PLANE_CONTRACTS } from './support/paths';
import { catalogToolDescriptorSchema } from '../../../src/modules/control-plane/contracts/generated/descriptors/tool';

type Json = Record<string, unknown>;
interface Vectors {
  tools: Array<{ description: string; descriptor: Json; digest: string }>;
  sets: Array<{
    description: string;
    descriptors: Array<{ name: string; digest: string }>;
    digest: string;
  }>;
  skills: Array<{
    description: string;
    files: Record<string, string>;
    manifest: Record<string, { sha256: string; size: number }>;
    digest: string;
  }>;
  engines: Array<{ description: string; engine: Json; digest: string }>;
  refs: {
    valid: Array<{
      ref: string;
      kind: string;
      publisher: string;
      name: string;
      version: string;
    }>;
    invalid: string[];
  };
}

const vectors = JSON.parse(
  readFileSync(
    join(CONTROL_PLANE_CONTRACTS, 'vectors', 'digests.json'),
    'utf8',
  ),
) as Vectors;

describe('control-plane catalog digests (CPD2-1..CPD2-6)', () => {
  for (const vector of vectors.tools) {
    it(`CPD2-1 tool: ${vector.description}`, () => {
      expect(toolDescriptorDigest(vector.descriptor)).to.equal(vector.digest);
    });
  }

  const pins = JSON.parse(
    readFileSync(
      join(
        CONTROL_PLANE_CONTRACTS,
        '..',
        'harness',
        'vectors',
        'descriptor-pins.json',
      ),
      'utf8',
    ),
  ) as {
    descriptor: Array<{ name: string; tool: Json; digest: string }>;
    change: Array<{
      name: string;
      base: { tool: Json; digest: string };
      changed: Array<{ name: string; tool: Json; digest: string }>;
      unchanged: Array<{ name: string; tool: Json }>;
    }>;
  };
  for (const vector of pins.descriptor) {
    it(`CPD2-1 the catalog digest is the run pin digest: ${vector.name}`, () => {
      expect(toolDescriptorDigest(vector.tool)).to.equal(vector.digest);
    });
  }

  it('QA-3 an MCP tool with behaviour hints is a valid catalog descriptor under its approval digest', () => {
    const vector = vectors.tools.find(
      (tool) => tool.description === 'MCP tool with behaviour hints',
    )!;
    const descriptor = { ...vector.descriptor, digest: vector.digest };
    expect(catalogToolDescriptorSchema.safeParse(descriptor).success).to.equal(
      true,
    );
    expect(
      catalogToolDescriptorSchema.safeParse({
        ...descriptor,
        annotations: { title: 'Pull requests' },
      }).success,
    ).to.equal(false);
    expect(toolDescriptorDigest(descriptor)).to.equal(vector.digest);
    expect(
      toolDescriptorDigest({ ...descriptor, annotations: undefined }),
    ).to.not.equal(vector.digest);
  });

  for (const family of pins.change) {
    it(`QA-3 PN-13 ${family.name}: every field that decides a call moves the digest`, () => {
      expect(toolDescriptorDigest(family.base.tool)).to.equal(
        family.base.digest,
      );
      for (const changed of family.changed) {
        expect(toolDescriptorDigest(changed.tool), changed.name).to.equal(
          changed.digest,
        );
      }
      const digests = [
        family.base.digest,
        ...family.changed.map((changed) => changed.digest),
      ];
      expect(new Set(digests).size).to.equal(digests.length);
    });

    it(`QA-3 PN-13 ${family.name}: provider and other annotations are outside the digest`, () => {
      for (const unchanged of family.unchanged) {
        expect(toolDescriptorDigest(unchanged.tool), unchanged.name).to.equal(
          family.base.digest,
        );
      }
    });
  }

  it('CPD2-1 member order, the digest member and absent effect/sink do not change a tool digest', () => {
    const tool = vectors.tools[0]!.descriptor;
    const reordered = Object.fromEntries(Object.entries(tool).reverse());
    expect(toolDescriptorDigest(reordered)).to.equal(
      toolDescriptorDigest(tool),
    );
    expect(
      toolDescriptorDigest({ ...tool, digest: `sha256:${'0'.repeat(64)}` }),
    ).to.equal(toolDescriptorDigest(tool));
    const bare = {
      name: 't',
      description: 'd',
      inputSchema: { type: 'object' },
    };
    expect(toolDescriptorDigest(bare)).to.equal(
      toolDescriptorDigest({ ...bare, effect: null, sink: null }),
    );
  });

  for (const vector of vectors.sets) {
    it(`CPD2-2 set: ${vector.description}`, () => {
      const pairs = vector.descriptors.map((d) => [d.name, d.digest] as const);
      expect(descriptorSetDigest(pairs)).to.equal(vector.digest);
      expect(descriptorSetDigest([...pairs].reverse())).to.equal(vector.digest);
    });
  }

  it('CPD2-2 a duplicate descriptor name is refused', () => {
    expect(() =>
      descriptorSetDigest([
        ['a', `sha256:${'1'.repeat(64)}`],
        ['a', `sha256:${'2'.repeat(64)}`],
      ]),
    ).to.throw(DuplicateDescriptorName, 'a');
  });

  for (const vector of vectors.skills) {
    it(`CPD2-3 skill: ${vector.description}`, () => {
      const files = new Map(
        Object.entries(vector.files).map(([path, b64]) => [
          path,
          Buffer.from(b64, 'base64'),
        ]),
      );
      expect(skillDescriptorDigest(vector.manifest)).to.equal(vector.digest);
      expect(skillBundleDigest(files)).to.equal(vector.digest);
    });
  }

  for (const vector of vectors.engines) {
    it(`CPD2-4 engine: ${vector.description}`, () => {
      expect(engineDigest(vector.engine)).to.equal(vector.digest);
    });
  }

  for (const vector of vectors.refs.valid) {
    it(`CPD2-5 ref round trip: ${vector.ref}`, () => {
      const ref = parseItemRef(vector.ref);
      expect(ref).to.deep.equal({
        kind: vector.kind,
        publisher: vector.publisher,
        name: vector.name,
        version: vector.version,
      });
      expect(formatItemRef(ref)).to.equal(vector.ref);
    });
  }

  it('CPD2-5 malformed refs are refused', () => {
    for (const ref of vectors.refs.invalid) {
      expect(() => parseItemRef(ref), ref).to.throw(InvalidItemRef);
    }
  });
});
