/**
 * R31-HOT, ADR-OWD-07 D-LAZY-8: a child of a parent that holds an MCP server whole (`x.pipeshub.mcp[].tools: at_first_use`,
 * ADR-OWD-07 D-LAZY-2) keeps it whole; one that names tools of it keeps exactly those; nothing widens.
 *
 * The owner's default assistant holds Atlassian Rovo whole, so its tool grants name none of Rovo's tools. The child's
 * MCP pins were derived from those grants alone, and every code_task job lost the server (`mcpServers=0`). The table
 * is `contracts/harness/vectors/child-mcp.json`, which the Python reference journal runs too.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  ChildGrantRequest,
  childFeatures,
  McpFirstUsePins,
  childGrants,
  childRunSpec,
  pipeshub,
} from '../../../src/modules/agent-harness/domain/child-runs';
import {
  engineExecutor,
  RELEASE_ENGINE_PROFILES,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import {
  MCP_FIRST_USE_FEATURE,
  PinnedRunSpec,
} from '../../../src/modules/agent-harness/domain/run-spec';

interface McpCase {
  id: string;
  behaviour: string;
  request: ChildGrantRequest;
  /** R31HOT2: the first-use pins the parent's worker carries into the spawn. */
  pins?: McpFirstUsePins;
  /** R31HOT2: the pins the parent's own spec carries (it inherited them). */
  parentSpecPins?: McpFirstUsePins;
  expect: {
    refusal: { code: string; dimension: string; items: string[] } | null;
    tools?: string[];
    childTools?: RunSpec['grants']['tools'];
    mcp?: unknown[];
  };
}

const table = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/child-mcp.json'),
    'utf8',
  ),
) as { parentSpec: RunSpec; cases: McpCase[] };

const pinnedOf = (spec: RunSpec): PinnedRunSpec => ({
  spec: structuredClone(spec),
  specDigest: `sha256:${'0'.repeat(64)}`,
  capabilities: {} as PinnedRunSpec['capabilities'],
  capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
});

/** The table's parent, its own MCP entries pinned as `parentSpecPins` says (a child that inherited a pin). */
const parentOf = (c: McpCase): RunSpec => {
  const spec = structuredClone(table.parentSpec);
  for (const server of pipeshub(spec)?.mcp ?? []) {
    const pin = c.parentSpecPins?.[server.instanceId];
    if (pin !== undefined) server.pins = { ...pin };
  }
  return spec;
};

describe('R31-HOT: a child keeps the MCP server its parent holds whole (ADR-OWD-07 D-LAZY-8)', () => {
  for (const c of table.cases) {
    it(`${c.id}: ${c.behaviour}`, () => {
      const parent = parentOf(c);
      const decision = childGrants(parent, c.request, 1, c.pins);
      expect(decision.refusal).to.deep.equal(c.expect.refusal);
      if (decision.grants === null) return;
      expect(decision.grants.tools).to.deep.equal(c.expect.tools);
      const child = childRunSpec(
        pinnedOf(parent), decision.grants, undefined, undefined, undefined, undefined, undefined, undefined,
        undefined, undefined, undefined, undefined, undefined, c.pins,
      ).spec;
      expect(child.grants.tools).to.deep.equal(c.expect.childTools);
      expect(pipeshub(child)?.mcp).to.deep.equal(c.expect.mcp);
    });
  }

  it('a code_task engine child (no tools named) holds the server whole, and needs a worker that lists at first use', () => {
    const decision = childGrants(table.parentSpec, { mounts: ['/workspace'] }, 1);
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(
      pinnedOf(table.parentSpec),
      decision.grants,
      undefined,
      undefined,
      engineExecutor(RELEASE_ENGINE_PROFILES[0]!),
    );
    const rovo = pipeshub(child.spec)?.mcp.find((server) => server.instanceId === 'mcp-rovo');
    expect(rovo?.tools).to.equal('at_first_use');
    expect(child.spec.grants.tools.some((grant) => grant.toolId.startsWith('mcp@mcp-rovo/'))).to.equal(false);
    expect(childFeatures(child)).to.include(MCP_FIRST_USE_FEATURE);
  });

  it('a child narrowed to named tools of the server is pinned to them alone: never whole, never another tool', () => {
    const decision = childGrants(table.parentSpec, { tools: ['mcp@mcp-rovo/getJiraIssue'] }, 1);
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(pinnedOf(table.parentSpec), decision.grants).spec;
    expect(pipeshub(child)?.mcp.every((server) => server.tools === undefined)).to.equal(true);
    expect(child.grants.tools.map((grant) => grant.toolId)).to.deep.equal(['mcp@mcp-rovo/getJiraIssue']);
    expect(childFeatures(pinnedOf(child))).to.not.include(MCP_FIRST_USE_FEATURE);
    // The narrowed child holds only that tool: its own child cannot take the server whole again.
    expect(childGrants(child, { tools: ['mcp@mcp-rovo/*'] }, 2).refusal?.items).to.deep.equal(['mcp@mcp-rovo/*']);
    expect(childGrants(child, { tools: ['mcp@mcp-rovo/createJiraIssue'] }, 2).refusal?.items).to.deep.equal([
      'mcp@mcp-rovo/createJiraIssue',
    ]);
  });

  it('a grandchild of a whole holder inherits it whole through its parent', () => {
    const first = childGrants(table.parentSpec, {}, 1);
    if (first.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(pinnedOf(table.parentSpec), { ...first.grants, maxChildren: 1 }).spec;
    const second = childGrants(child, {}, 2);
    if (second.grants === null) expect.fail('expected a grant');
    const grandchild = childRunSpec(pinnedOf(child), second.grants).spec;
    expect(pipeshub(grandchild)?.mcp).to.deep.equal(pipeshub(table.parentSpec)?.mcp);
  });
});
