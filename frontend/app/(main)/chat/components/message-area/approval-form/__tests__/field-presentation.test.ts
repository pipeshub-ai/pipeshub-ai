import { describe, expect, it } from 'vitest';
import { documentLayout } from '../document-layout';
import { argumentFields, fieldLabel, isTechnical, schemaFields } from '../schema-fields';
import { approvalProduct } from '../../../../tool-approval';

describe('fieldLabel', () => {
  it("prefers the schema's title", () => {
    expect(fieldLabel('issueTypeName', 'Work type')).toBe('Work type');
    expect(fieldLabel('issueTypeName', '  ')).toBe('Issue type');
  });

  it('turns camelCase and snake_case into sentence case, dropping a trailing name or key', () => {
    expect(fieldLabel('issueTypeName')).toBe('Issue type');
    expect(fieldLabel('projectKey')).toBe('Project');
    expect(fieldLabel('parent_issue_key')).toBe('Parent issue');
    expect(fieldLabel('summary')).toBe('Summary');
    expect(fieldLabel('mail_to')).toBe('Mail to');
    expect(fieldLabel('name')).toBe('Name');
    expect(fieldLabel('key')).toBe('Key');
  });

  it('writes ids and links as initialisms', () => {
    expect(fieldLabel('cloudId')).toBe('Cloud ID');
    expect(fieldLabel('accountIds')).toBe('Account IDs');
    expect(fieldLabel('html_url')).toBe('HTML URL');
  });
});

describe('isTechnical', () => {
  const field = (name: string, schema: object = { type: 'string' }) => schemaFields({ type: 'object', properties: { [name]: schema } })[0];

  it('is an id by name, by uuid format or by a uuid-like value', () => {
    expect(isTechnical(field('cloudId'), 'abc')).toBe(true);
    expect(isTechnical(field('parent_id'), 'x')).toBe(true);
    expect(isTechnical(field('ref', { type: 'string', format: 'uuid' }), 'x')).toBe(true);
    expect(isTechnical(field('site'), 'c7f37664-a798-44ef-a525-ecacfeb1087d')).toBe(true);
  });

  it('is not a field whose name only ends in the letters', () => {
    expect(isTechnical(field('paid', { type: 'boolean' }), true)).toBe(false);
    expect(isTechnical(field('summary'), 'Fix login')).toBe(false);
    expect(isTechnical(field('projectKey'), 'PP')).toBe(false);
  });
});

describe('argumentFields', () => {
  it('keeps every argument: schema fields first, then the ones the schema left out', () => {
    const fields = argumentFields(
      { type: 'object', properties: { summary: { type: 'string', title: 'Summary' } } },
      { summary: 'Fix', count: 2, labels: ['a'], flag: true, meta: { a: 1 } },
    );
    expect(fields.map((f) => [f.name, f.label, f.control])).toEqual([
      ['summary', 'Summary', 'text'],
      ['count', 'Count', 'number'],
      ['labels', 'Labels', 'chips'],
      ['flag', 'Flag', 'switch'],
      ['meta', 'Meta', 'json'],
    ]);
  });

  it('builds fields from the values alone when there is no schema', () => {
    expect(argumentFields(undefined, { description: 'Long text' }).map((f) => f.control)).toEqual(['textarea']);
  });
});

describe('documentLayout with values', () => {
  it('the Jira call: summary headline, description body, project and type as metadata, the cloud id in details', () => {
    const values = {
      cloudId: 'c7f37664-a798-44ef-a525-ecacfeb1087d',
      projectKey: 'PP',
      issueTypeName: 'Task',
      summary: 'Test Slack automation and tool approvals',
      description: 'Test Slack automation workflows.',
    };
    const l = documentLayout(argumentFields(undefined, values), values);
    expect(l.headline?.name).toBe('summary');
    expect(l.body.map((f) => f.name)).toEqual(['description']);
    expect(l.pills.map((f) => f.label)).toEqual(['Project', 'Issue type']);
    expect(l.more.map((f) => f.label)).toEqual(['Cloud ID']);
  });
});

describe('approvalProduct', () => {
  it('names the product a tool works in when the server is a hub for several', () => {
    expect(approvalProduct({ toolName: 'createJiraIssue', serverName: 'Atlassian Rovo' })).toBe('Jira');
    expect(approvalProduct({ toolName: 'createConfluencePage', serverName: 'Atlassian' })).toBe('Confluence');
  });

  it("keeps the server's name when the tool names no product, or the server already does", () => {
    expect(approvalProduct({ toolName: 'create_issue', serverName: 'Jira (Engineering)' })).toBe('Jira (Engineering)');
    expect(approvalProduct({ toolName: 'jira_create_issue', serverName: 'Jira' })).toBe('Jira');
    expect(approvalProduct({ toolName: 'send_email', serverName: 'Mail' })).toBe('Mail');
  });
});
