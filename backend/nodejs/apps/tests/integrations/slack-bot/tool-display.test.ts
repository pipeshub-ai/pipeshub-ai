import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  describeTool,
  humanizeToolName,
  toolActivityLabel,
  toolStatusLabel,
} from '../../../src/integrations/slack-bot/src/utils/tool-display';
import { foldActivity } from '../../../src/modules/agent-harness/projection/activity';
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector';
import { buildCompletion } from '../../../src/modules/agent-harness/projection/transcript';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { GoldenJournal, liveSteps, projectorContext, readJournal, runSteps } from '../../contracts/support/durable';

interface Case {
  id: string;
  in: { toolName: string; displayName?: string };
  out: { name: string; app: string | null; label: string; present: string; displayName: string };
}

const vectors = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/tool-display-names.json'), 'utf-8'),
) as { cases: Case[] };

describe('TOOLNAMES: a step names its tool in words (shared vectors with the web client)', () => {
  for (const c of vectors.cases) {
    it(c.id, () => {
      const got = describeTool(c.in.toolName, c.in.displayName);
      expect({ name: got.name, app: got.app ?? null, label: got.label, present: got.present, displayName: got.displayName }).to.deep.equal(c.out);
      // The one-line name names the tool the same way again.
      const again = describeTool(c.in.toolName, got.displayName);
      expect({ name: again.name, app: again.app ?? null, label: again.label }).to.deep.equal({ name: c.out.name, app: c.out.app, label: c.out.label });
      for (const shown of [got.name, got.label, got.present, got.displayName, got.app ?? '']) {
        expect(shown, shown).to.not.match(/__|@/);
      }
    });
  }

  it('humanizes an id as words, sentence case, acronyms kept', () => {
    expect(humanizeToolName('dynamic__web_search')).to.equal('Web search');
    expect(humanizeToolName('image_generator__generate_image')).to.equal('Generate image');
    expect(humanizeToolName('pipeshub@sandbox/run_code')).to.equal('Run code');
    expect(humanizeToolName('export-pdf-report')).to.equal('Export PDF report');
  });

  it("Slack's status and activity lines read the same words", () => {
    expect(toolStatusLabel('dynamic__web_search')).to.equal('Searching the web');
    expect(toolActivityLabel('image_generator__generate_image')).to.equal('Generated an image');
    expect(toolActivityLabel('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket')).to.equal('E2Esaas · Create ticket');
    expect(toolStatusLabel('jira__create_issue')).to.equal('Using Jira · Create issue');
  });
});

describe('TOOLNAMES: the projector carries the tool’s display name and its id to every reader', () => {
  // The worker's display name (an MCP server's "Server · tool") on the recorded call.
  const journal = ((): GoldenJournal => {
    const golden = readJournal('agent_steered_during_tool');
    const events = golden.events.map((e) =>
      e.type === 'x.pipeshub.tool.call_presented'
        ? ({ ...e, data: { ...(e.data as Record<string, unknown>), displayName: 'Golden Docs · export_report' } } as typeof e)
        : e,
    );
    return { ...golden, events };
  })();
  const SHOWN = { name: 'Export report', app: 'Golden Docs' };

  it('the live TOOL_CALL_START', () => {
    const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal)).map((f) => f.frame);
    const start = frames.find((f) => f.type === 'TOOL_CALL_START')!;
    expect(start).to.deep.include({ toolCallName: 'goldendocs__export_report', displayName: 'Golden Docs · export_report' });
    expect(describeTool(start['toolCallName'] as string, start['displayName'] as string)).to.deep.include(SHOWN);
  });

  it('the saved part and the activity drawer, the raw id kept beside it for the drawer’s detail', () => {
    const parts = (buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: Array<Record<string, unknown>> }).parts;
    const part = parts.find((p) => p['type'] === 'tool_call')!;
    expect(part).to.deep.include({ toolName: 'goldendocs__export_report', displayName: 'Golden Docs · export_report' });
    const [tool] = foldActivity(journal.events, { runId: journal.runId }).tools;
    expect(tool).to.deep.include({ name: 'goldendocs__export_report', displayName: 'Golden Docs · export_report' });
    expect(describeTool(tool!.name, tool!.displayName)).to.deep.include(SHOWN);
  });

  it('a call recorded without one still names its tool in words from its id', () => {
    const golden = readJournal('agent_steered_during_tool');
    const [tool] = foldActivity(golden.events, { runId: golden.runId }).tools;
    expect(tool!.displayName).to.equal(undefined);
    expect(describeTool(tool!.name, tool!.displayName)).to.deep.include({ name: 'Export report', app: 'Goldendocs', label: 'Export report' });
  });
});
