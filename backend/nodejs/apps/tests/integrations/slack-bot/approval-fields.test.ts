import { expect } from 'chai';
import {
  approvalLayout,
  approvalProduct,
  fieldLabel,
} from '../../../src/integrations/slack-bot/src/utils/approval-fields';

// The same examples as the web card's tests (frontend approval-form/__tests__/field-presentation.test.ts).
describe('slack-bot/utils/approval-fields', () => {
  it("labels a field by the schema's title, else its name in sentence case", () => {
    expect(fieldLabel('issueTypeName', 'Work type')).to.equal('Work type');
    expect(
      ['issueTypeName', 'projectKey', 'parent_issue_key', 'summary', 'mail_to', 'name', 'key', 'cloudId', 'accountIds', 'html_url'].map((n) =>
        fieldLabel(n),
      ),
    ).to.deep.equal(['Issue type', 'Project', 'Parent issue', 'Summary', 'Mail to', 'Name', 'Key', 'Cloud ID', 'Account IDs', 'HTML URL']);
  });

  it('names the product a hub server acts in, else the server', () => {
    expect(approvalProduct('createJiraIssue', 'Atlassian Rovo')).to.equal('Jira');
    expect(approvalProduct('createConfluencePage', 'Atlassian')).to.equal('Confluence');
    expect(approvalProduct('create_issue', 'Jira (Engineering)')).to.equal('Jira (Engineering)');
    expect(approvalProduct('jira_create_issue', 'Jira')).to.equal('Jira');
  });

  it('lays out the Jira call as the web card does', () => {
    const l = approvalLayout(
      {
        cloudId: 'c7f37664-a798-44ef-a525-ecacfeb1087d',
        projectKey: 'PP',
        issueTypeName: 'Task',
        summary: 'Test Slack automation and tool approvals',
        description: 'Test Slack automation workflows.',
        paid: true,
      },
      undefined,
    );
    expect(l.headline?.name).to.equal('summary');
    expect(l.body.map((f) => f.name)).to.deep.equal(['description']);
    expect(l.meta.map((f) => f.label)).to.deep.equal(['Project', 'Issue type', 'Paid']);
    expect(l.details.map((f) => f.label)).to.deep.equal(['Cloud ID']);
  });
});
