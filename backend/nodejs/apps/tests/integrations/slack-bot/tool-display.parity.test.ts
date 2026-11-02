import 'reflect-metadata';
import { expect } from 'chai';
// The dashboard's own helpers: the Slack trail must label every tool the way it does.
import * as web from '../../../../../../frontend/app/(main)/chat/utils/tool-display';
import * as slack from '../../../src/integrations/slack-bot/src/utils/tool-display';

/** The tools a chat can call, across every kind the dashboard draws, and one it has never seen. */
const TOOLS: [string, string | undefined][] = [
  ['web_search', 'Searched the web'],
  ['dynamic__fetch_url', 'Read a web page'],
  ['knowledgegraph__search', 'Searched the knowledge base'],
  ['jira__search_issues', undefined],
  ['confluence__get_page', 'Read a Confluence page'],
  ['slack__fetch_slack_thread', undefined],
  ['gmail__send_email', undefined],
  ['outlook__list_calendar_events', undefined],
  ['google_drive__get_file', undefined],
  ['github__create_issue', 'Created a GitHub issue'],
  ['clickup__create_task', undefined],
  ['salesforce__soql_query', undefined],
  ['mcp_jira_search_issues', 'Jira · search_issues'],
  ['run_code', undefined],
  ['install_packages', undefined],
  ['read_sandbox_file', undefined],
  ['load_skill', undefined],
  ['skill_search', undefined],
  ['ask_user_question', undefined],
  ['artifacts__create_artifact', undefined],
  ['some_brand_new_tool', undefined],
];

/** The dashboard's group label (ToolCallGroup in agent-activity.tsx). */
function webGroupLabel(names: string[]): string {
  if (names.every((name) => web.isSkillTool(name)))
    return `Used ${String(names.length)} skills`;
  if (names.every((name) => /search/i.test(name)))
    return `Explored ${String(names.length)} searches`;
  return `Ran ${String(names.length)} tools`;
}

describe('slack-bot tool display matches the dashboard', () => {
  for (const [name, displayName] of TOOLS) {
    it(`labels ${name} the same`, () => {
      expect(slack.toolActivityLabel(name, displayName)).to.equal(
        web.toolActivityLabel(name, displayName),
      );
      expect(slack.toolActivityLabel(name)).to.equal(
        web.toolActivityLabel(name),
      );
      expect(slack.extractToolsetLabel(name)).to.equal(
        web.extractToolsetLabel(name),
      );
      expect(slack.isSkillTool(name)).to.equal(web.isSkillTool(name));
      expect(slack.isWebTool(name)).to.equal(web.isWebTool(name));
      expect(slack.isMcpTool(name)).to.equal(web.isMcpTool(name));
      expect(slack.humanizeToolName(name)).to.equal(web.humanizeToolName(name));
    });
  }

  it('groups calls under the same label', () => {
    const bursts = [
      ['web_search', 'knowledgegraph__search', 'jira__search_issues'],
      ['load_skill', 'skill_search'],
      ['run_code', 'web_search'],
      ['dynamic__fetch_url', 'dynamic__fetch_url'],
    ];
    for (const names of bursts) {
      expect(slack.toolGroupLabel(names)).to.equal(webGroupLabel(names));
    }
  });
});
