/**
 * TR-04, TR-07: the release's trigger settings every org runs under, and the
 * digest a run-ended notice carries.
 */
import { expect } from 'chai';
import { RELEASE_TRIGGER_SETTINGS } from '../../../src/modules/agent-harness/application/release-run-settings';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import {
  MAX_NOTICE_ARTIFACTS,
  MAX_NOTICE_SUMMARY_CHARS,
  runEndDigest,
} from '../../../src/modules/agent-harness/domain/run-end-notice';

describe('TR-04/TR-07 the release trigger settings', () => {
  it("TR-04: every org's are the release's: 256 KiB, 30 a minute, every channel", async () => {
    expect(await RELEASE_TRIGGER_SETTINGS.webhookMaxBytes('org-1')).to.equal(262_144);
    expect(await RELEASE_TRIGGER_SETTINGS.webhookPerMinute('org-1')).to.equal(30);
    expect(await RELEASE_TRIGGER_SETTINGS.notifyChannels('org-1')).to.deep.equal(RELEASE_DEFAULTS.triggers.notifyChannels);
  });
});

describe('TR-07 runEndDigest', () => {
  it('TR-07: the answer as plain words, its files as links, each once', () => {
    const marker = (name: string, id: string) => `::artifact[${name}](record:${id}){text/csv|d|${id}|ARTIFACT|1}`;
    const digest = runEndDigest(
      `# Weekly\n\n- **Two** items in \`queue\`\n\n${marker('a.csv', 'r1')}\n${marker('a.csv', 'r1')}\n${marker('b c.csv', 'r 2')}`,
    );
    expect(digest).to.deep.equal({
      summary: 'Weekly Two items in queue',
      artifacts: [
        { name: 'a.csv', link: '/record/r1/' },
        { name: 'b c.csv', link: '/record/r%202/' },
      ],
    });
  });

  it('TR-07: a long answer is cut at a word with an ellipsis; at most the first files are linked', () => {
    const digest = runEndDigest(
      `${'word '.repeat(200)}${Array.from({ length: 15 }, (_, i) => `::artifact[f${String(i)}](record:r${String(i)}){x}`).join('\n')}`,
    );
    expect(digest.summary.length).to.be.at.most(MAX_NOTICE_SUMMARY_CHARS);
    expect(digest.summary.endsWith('word…')).to.equal(true);
    expect(digest.artifacts).to.have.length(MAX_NOTICE_ARTIFACTS);
    expect(runEndDigest('')).to.deep.equal({ summary: '', artifacts: [] });
  });
});
