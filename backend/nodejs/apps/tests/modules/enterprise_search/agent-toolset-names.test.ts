/**
 * CPD2-60: the toolset names the create-agent gateway accepts come from the
 * release catalog (its listed toolsets), no longer from a hand-written list.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import yaml from 'js-yaml';
import {
  AGENT_CREATE_TOOLSET_NAMES,
  createAgentSchema,
} from '../../../src/modules/enterprise_search/validators/es_validators';

// The hand-written list this replaced, which the catalog must still produce, plus main's Docs and
// Sheets toolsets (#4221).
const BEFORE_CPD2 = [
  'calendar',
  'clickup',
  'confluence',
  'confluencedatacenter',
  'docs',
  'drive',
  'github',
  'gmail',
  'jira',
  'jiradatacenter',
  'lumos',
  'mariadb',
  'onedrive',
  'outlook',
  'redshift',
  'salesforce',
  'sharepoint',
  'sheets',
  'slack',
  'teams',
  'zoom',
];

const withToolset = (name: string) =>
  createAgentSchema.safeParse({
    body: {
      name: 'Agent',
      models: [{ modelKey: 'mk', modelName: 'mn', isReasoning: true }],
      toolsets: [{ name }],
    },
  });

describe('create-agent toolset names from the catalog (CPD2-60)', () => {
  it('are the listed toolsets of the release catalog, the same names as before', () => {
    expect([...AGENT_CREATE_TOOLSET_NAMES]).to.deep.equal(BEFORE_CPD2);
  });

  it('are each accepted (trimmed); an unknown, internal or capitalised name is refused as before', () => {
    for (const name of AGENT_CREATE_TOOLSET_NAMES)
      expect(withToolset(name).success, name).to.equal(true);
    expect(withToolset(' jira ').success).to.equal(true);
    for (const name of ['unknown_toolset', 'calculator', 'retrieval', 'Jira']) {
      const result = withToolset(name);
      expect(result.success, name).to.equal(false);
      expect(result.error?.issues[0]?.message).to.equal('Invalid toolset name');
    }
  });

  it('equal the OpenAPI AgentCreateToolsetName enum', () => {
    const spec = yaml.load(
      readFileSync(
        join(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'),
        'utf8',
      ),
    ) as { components: { schemas: Record<string, { enum?: string[] }> } };
    expect(spec.components.schemas.AgentCreateToolsetName?.enum).to.deep.equal([
      ...AGENT_CREATE_TOOLSET_NAMES,
    ]);
  });
});
