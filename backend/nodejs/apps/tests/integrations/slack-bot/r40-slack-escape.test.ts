import 'reflect-metadata';
import { expect } from 'chai';
import { markdownToSlackMrkdwn, markdownToText } from '../../../src/integrations/slack-bot/src/utils/md_to_mrkdwn';
import {
  escapeSlackText,
  safeLinkLabel,
  slackLink,
} from '../../../src/integrations/slack-bot/src/utils/slack-escape';
import { formatAskUserQuestionMrkdwn } from '../../../src/integrations/slack-bot/src/utils/ask-user-format';
import { SlackActivityBuilder } from '../../../src/integrations/slack-bot/src/utils/activity-ui';
import { handOffText } from '../../../src/integrations/slack-bot/src/utils/hand-off';
import { citationSourceBlocks } from '../../../src/integrations/slack-bot/src/utils/finished-answer';
import {
  buildFinalSlackChunks,
  buildSlackTableCellElements,
} from '../../../src/integrations/slack-bot/src/helpers';

/**
 * SEC-B-03 bypass variants. Slack acts on `<…>`: a token starting with `!`, `@` or `#` notifies or links, and
 * `<url|label>` shows any label. A live token is one Slack would parse; none may come from untrusted text, and every
 * link label that reads as an address must name the link's own host.
 */
function liveTokens(mrkdwn: string): string[] {
  return [...mrkdwn.matchAll(/<([^<>]*)>/g)].map((m) => m[1] ?? '');
}

function expectNoControlToken(mrkdwn: string): void {
  for (const token of liveTokens(mrkdwn)) {
    expect(token, `live Slack token <${token}>`).not.to.match(/^\s*[!@#]/);
    const [target, label] = token.split('|', 2);
    if (label === undefined) continue;
    const host = new URL(target ?? '').hostname;
    const claimed = label.match(/^(?:[a-z]+:\/\/)?([^\s/]+\.[^\s/]+)/i)?.[1];
    if (claimed) expect(claimed.toLowerCase(), `label "${label}" on ${host}`).to.equal(host);
  }
}

describe('R40-SLACK SEC-B-03: no Slack control syntax from untrusted text', () => {
  describe('mentions and broadcasts', () => {
    const tokens = [
      '<!channel>',
      '<!here>',
      '<!everyone>',
      '<!subteam^S0ADMINS|@admins>',
      '<!date^1392734382^{date}|Feb 18>',
      '<@U0CFO>',
      '<@W0GRID>',
      '<#C0GENERAL|general>',
    ];
    for (const token of tokens) {
      it(`${token} is shown, not acted on`, () => {
        const out = markdownToSlackMrkdwn(`Done. ${token} please re-auth`);
        expectNoControlToken(out);
        expect(out).to.contain(escapeSlackText(token));
      });
    }
  });

  it('an entity-encoded token stays text: the & is escaped, so &lt; never decodes into <', () => {
    const out = markdownToSlackMrkdwn('&lt;!channel&gt; and &amp;lt;!here&amp;gt;');
    expectNoControlToken(out);
    expect(out).to.contain('&amp;lt;!channel&amp;gt;');
    expect(out).not.to.match(/(?<!&amp;)&lt;!channel/);
  });

  it('nested tokens are escaped at every level', () => {
    for (const text of ['<<!here>>', '<<@U1>|x>', '<https://a.example|<!channel>>', '[<!channel> hi](https://x.example)']) {
      expectNoControlToken(markdownToSlackMrkdwn(text));
    }
  });

  it('zero-width and bidi characters inside a token do not slip it through', () => {
    for (const text of ['<!\u200bchannel>', '<\u200b@U1>', '<\u202e!here>', '<\ufeff#C1>']) {
      const out = markdownToSlackMrkdwn(text);
      expectNoControlToken(out);
      expect(out).not.to.contain('<');
    }
  });

  it('fullwidth brackets are not Slack syntax and pass as text', () => {
    const out = markdownToSlackMrkdwn('＜!channel＞');
    expect(out).to.equal('＜!channel＞');
    expectNoControlToken(out);
  });

  it('a NUL cannot spell one of the converter placeholders', () => {
    const out = markdownToSlackMrkdwn('`x` \x00PLACEHOLDER_0\x00 <!here>');
    expectNoControlToken(out);
    expect(out).not.to.contain('\x00');
  });

  it('code spans, fences and tables are escaped too', () => {
    for (const text of ['`<!here>`', '```\n<!channel>\n<@U1>\n```', '| a |\n|---|\n| <!here> |']) {
      const out = markdownToSlackMrkdwn(text);
      expectNoControlToken(out);
      expect(out).to.contain('&lt;');
    }
    expectNoControlToken(markdownToSlackMrkdwn('| a |\n|---|\n| <!here> |', { tableMode: 'text' }));
  });

  describe('links', () => {
    it('a label that reads as another host shows the real target', () => {
      expect(markdownToSlackMrkdwn('[https://pipeshub.example/login](https://attacker.example/login)')).to.equal(
        '<https://attacker.example/login|attacker.example/login>',
      );
      for (const label of [
        'pipeshub.example',
        'www.pipeshub.example/sso',
        'https://pipe\u200bshub.example',
        'https://p\u0456peshub.example', // a Cyrillic i: a lookalike host is another host
        'HTTPS://PIPESHUB.EXAMPLE',
      ]) {
        expectNoControlToken(markdownToSlackMrkdwn(`[${label}](https://attacker.example/x)`));
        expect(markdownToSlackMrkdwn(`[${label}](https://attacker.example/x)`)).to.contain('|attacker.example/x>');
      }
    });

    it('Slack-style links in the text are rebuilt under the same rule', () => {
      expect(markdownToSlackMrkdwn('<https://attacker.example/login|https://pipeshub.example/login>')).to.equal(
        '<https://attacker.example/login|attacker.example/login>',
      );
      expect(markdownToSlackMrkdwn('see <https://docs.example/a>')).to.equal('see <https://docs.example/a|docs.example/a>');
    });

    it('an ordinary link keeps its label; a label naming the target host is kept', () => {
      expect(markdownToSlackMrkdwn('[Docs](https://docs.example/x)')).to.equal('<https://docs.example/x|Docs>');
      expect(markdownToSlackMrkdwn('[1](https://pipeshub.example/record/r1)')).to.equal('<https://pipeshub.example/record/r1|1>');
      expect(markdownToSlackMrkdwn('[https://pipeshub.example/a](https://pipeshub.example/b)')).to.equal(
        '<https://pipeshub.example/b|https://pipeshub.example/a>',
      );
    });

    it("Slack's delimiters in a URL or label cannot end the link early", () => {
      const out = markdownToSlackMrkdwn('[a](https://x.example/a|<!here>)');
      expectNoControlToken(out);
      expect(out).to.match(/^<https:\/\/x\.example\/a%7C/);
      expect(markdownToSlackMrkdwn('[a](https://x.example/?a=1&b=2)')).to.equal('<https://x.example/?a=1&amp;b=2|a>');
      expect(slackLink('https://x.example/', 'a > b <!here>')).to.equal('<https://x.example/|a &gt; b &lt;!here&gt;>');
    });

    it('a target that is not a web or mail address is never clickable', () => {
      for (const text of ['[click](javascript:alert(1))', '[x](slack://open)', '[x](data:text/html,hi)']) {
        const out = markdownToSlackMrkdwn(text);
        expect(liveTokens(out), out).to.deep.equal([]);
      }
    });

    it('safeLinkLabel: an empty label names the target', () => {
      expect(safeLinkLabel('https://a.example/p', '')).to.equal('a.example/p');
      expect(safeLinkLabel('https://a.example/', undefined)).to.equal('a.example');
    });
  });

  it('blockquotes still render', () => {
    expect(markdownToSlackMrkdwn('> quoted <!here>')).to.equal('> quoted &lt;!here&gt;');
  });

  it('markdownToText leaves no token', () => {
    expectNoControlToken(markdownToText('hi <!channel> <@U1> &lt;!here&gt;'));
  });

  describe('every post path', () => {
    it('the ask-a-question message escapes the intent and the questions', () => {
      const out = formatAskUserQuestionMrkdwn({
        toolData: {
          userIntent: '<!channel>',
          questions: [{ uuid: 'q1', question: '<@U0CFO> approve <https://a.example|https://pipeshub.example>?', options: [] }],
        },
      });
      expectNoControlToken(out ?? '');
      expect(out).to.contain('&lt;!channel&gt;');
    });

    it('the activity timeline escapes tool labels, sub-agent roles and the status line', () => {
      const activity = new SlackActivityBuilder();
      activity.startTool('t1', 'Using <!here>');
      activity.finishTool('t1', 'Using <!here>');
      activity.startSubAgent('<@U0CFO>');
      activity.setStatus('<!channel>...');
      activity.appendNarration('narration <!everyone>');
      const out = activity.format();
      expectNoControlToken(out);
      expect(out).to.contain('&lt;!here&gt;');
    });

    it('the hand-off names a tool without its tokens and keeps its own link', () => {
      const out = handOffText(
        { conversationId: 'c1', waitingOn: [{ toolName: 'x', displayName: 'Evil · <!channel> <https://a.example|ok>' }] },
        'https://p.example',
      );
      expect(liveTokens(out)).to.deep.equal(['https://p.example/chat/?conversationId=c1|Open this conversation in PipesHub']);
    });

    it('a source named like another host shows its real address', () => {
      const blocks = citationSourceBlocks([
        {
          citationId: 'c1',
          citationData: {
            chunkIndex: 1,
            metadata: { recordId: 'r1', recordName: 'https://pipeshub.example/login', webUrl: 'https://attacker.example/x' },
          },
        } as never,
      ]);
      const link = JSON.stringify(blocks);
      expect(link).not.to.contain('pipeshub.example/login');
      expect(link).to.contain('attacker.example/x');
    });

    it('table cells: a rich-text link labelled as another host shows the target', () => {
      const elements = buildSlackTableCellElements('[https://pipeshub.example](https://attacker.example/x)', false);
      const link = elements.find((e) => e.type === 'link');
      expect(link?.url).to.equal('https://attacker.example/x');
      expect(link?.text).to.not.contain('pipeshub');
    });

    it('the final answer blocks carry no token: mrkdwn is escaped, rich text holds no mention element', async () => {
      const chunks = await buildFinalSlackChunks('Hi <!channel>\n\n| a |\n|---|\n| <@U1> |\n\n<!here>');
      const json = JSON.stringify(chunks);
      const mrkdwn = [...json.matchAll(/"type":"mrkdwn","text":"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`) as string);
      expect(mrkdwn).to.have.length.greaterThan(0);
      for (const text of mrkdwn) expectNoControlToken(text);
      // Rich-text "text" elements are literal; Slack mentions there only through these element types.
      expect(json).not.to.match(/"type":"(?:user|usergroup|broadcast|channel)"/);
    });
  });
});
