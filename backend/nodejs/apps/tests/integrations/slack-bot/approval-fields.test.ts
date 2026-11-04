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

  it('puts format switches set to a format name under Details, as the web card does', () => {
    const l = approvalLayout(
      { contentFormat: 'markdown', responseContentFormat: 'adf', body_format: 'HTML', imageFormat: 'png', format: 'Quarterly report' },
      undefined,
    );
    expect(l.details.map((f) => f.name)).to.deep.equal(['contentFormat', 'responseContentFormat', 'body_format']);
    expect(l.meta.map((f) => f.name)).to.deep.equal(['imageFormat', 'format']);
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

  it('takes the title by the last word, as the web card does: mail_subject, event_title, topic; name only whole', () => {
    const gmail = approvalLayout({ mail_to: ['sam@example.com'], mail_subject: 'Leave request', mail_body: 'Hi Sam' }, undefined);
    expect(gmail.headline?.name).to.equal('mail_subject');
    expect(gmail.body.map((f) => f.name)).to.deep.equal(['mail_body']);
    expect(gmail.meta).to.deep.equal([]);
    expect(approvalLayout({ event_start_time: '10:00', event_title: 'Design review' }, undefined).headline?.name).to.equal('event_title');
    const zoom = approvalLayout({ topic: 'Weekly sync', agenda: 'Status' }, undefined);
    expect([zoom.headline?.name, zoom.body.map((f) => f.name)]).to.deep.equal(['topic', ['agenda']]);
    const named = approvalLayout({ assignee_name: 'Sam', file_name: 'a.txt' }, undefined);
    expect([named.headline, named.meta.map((f) => f.name)]).to.deep.equal([null, ['assignee_name', 'file_name']]);
  });

  it('reads code, a query and a prompt to run as text, but not a product code or a search term', () => {
    const l = approvalLayout({ query: 'select 1', code: 'print(1)', prompt: 'A cat', product_code: 'X1', assignee_query: 'sam' }, undefined);
    expect(l.body.map((f) => f.name)).to.deep.equal(['query', 'code', 'prompt']);
    expect(l.meta.map((f) => f.name)).to.deep.equal(['product_code', 'assignee_query']);
  });
});
