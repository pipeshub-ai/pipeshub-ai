import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import * as mdToMrkdwn from '../../../src/integrations/slack-bot/src/utils/md_to_mrkdwn';
import {
  userInfoCache,
  getCachedUserInfo,
  setCachedUserInfo,
  USER_INFO_CACHE_TTL_MS,
  isSlackSupportedAttachment,
  isSlackOversizedAttachment,
  classifySlackFiles,
  extractSupportedAttachments,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENT_MB,
  TEXT_ATTACHMENT_EXTENSION_MIMETYPES,
  SUPPORTED_ATTACHMENTS_HINT,
  resolveTextAttachmentMimetype,
  isReadableUtf8Text,
  uploadSlackAttachments,
  buildSkippedAttachmentsNotice,
  postSkippedAttachmentsNotice,
  handleIncomingAttachments,
  messageHasQuestionText,
  parseSSEEvents,
  readMessageFromObject,
  readMessageFromTextPayload,
  extractSlackErrorMessage,
  normalizeSlackErrorMessage,
  formatSlackErrorMessage,
  isReadableStreamLike,
  truncateForSlack,
  truncateForSlackStreamMarkdown,
  truncateFromEnd,
  resolveThreadId,
  splitByLengthPreferringNewlines,
  describeSlackFile,
  isMarkdownTableRow,
  isMarkdownTableSeparatorRow,
  parseMarkdownTableCells,
  getFenceMarker,
  isFenceClosingLine,
  tryParseMarkdownTableAtLine,
  hasMarkdownTableStartOutsideCodeFences,
  splitMarkdownMessageIntoTableAwareSegments,
  splitTrailingPunctuationFromUrl,
  buildFrontendRecordUrl,
  resolveSlackArtifactLink,
  buildChatStreamUrl,
  buildSlackRichTextTextElement,
  buildSlackRichTextLinkElement,
  buildSlackTableCellElements,
  buildSlackTableBlock,
  splitSectionTextObjectByLimit,
  splitSectionFieldsByLimit,
  splitSectionBlockForSlackLimits,
  normalizeSlackBlocksForLimits,
  getBlockPayloadTextSize,
  splitSlackBlocksByLimit,
  isThreadFollowUpMessage,
  sanitizeSlackLabelValue,
  pickSlackDisplayName,
  formatMentionedUser,
  formatSlackUserLabel,
  slackCallerDisplayName,
  inferThreadMessageSpeaker,
  removeContinuousDuplicateMarkdownLinks,
  addSpaceBetweenMarkdownLinks,
  isIgnoredSlackMessage,
  SLACK_MAX_TEXT_LENGTH,
  SLACK_STREAM_MARKDOWN_LIMIT,
  DEFAULT_SLACK_ERROR_MESSAGE,
  MAX_USER_VISIBLE_ERROR_LENGTH,
  SLACK_SECTION_TEXT_LIMIT,
  SLACK_SECTION_FIELD_TEXT_LIMIT,
  SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT,
  SLACK_BLOCKS_PER_MESSAGE_LIMIT,
  SLACK_BLOCKS_TOTAL_TEXT_LIMIT,
  MAX_TABLE_ROWS,
  MAX_TABLE_COLS,
  type SlackFile,
  type SlackMessagePayload,
  type SlackUserRecord,
} from '../../../src/integrations/slack-bot/src/helpers';

describe('slack-bot/helpers', () => {
  let markdownToTextStub: sinon.SinonStub;

  beforeEach(() => {
    userInfoCache.clear();
    markdownToTextStub = sinon.stub(mdToMrkdwn, 'markdownToText').callsFake((s) => s);
  });

  afterEach(() => {
    sinon.restore();
    delete process.env.BACKEND_URL;
    delete process.env.FRONTEND_PUBLIC_URL;
  });

  // -----------------------------------------------------------------------
  // User info cache
  // -----------------------------------------------------------------------
  describe('user info cache', () => {
    const userId = 'U12345';
    const userRecord: SlackUserRecord = {
      id: userId,
      name: 'testuser',
      profile: { email: 'test@example.com' },
    };

    it('getCachedUserInfo returns null when user is not cached', () => {
      expect(getCachedUserInfo('UNKNOWN')).to.equal(null);
    });

    it('setCachedUserInfo stores and getCachedUserInfo retrieves a user', () => {
      setCachedUserInfo(userId, userRecord);
      expect(getCachedUserInfo(userId)).to.deep.equal(userRecord);
    });

    it('getCachedUserInfo returns undefined when user was stored as undefined', () => {
      setCachedUserInfo(userId, undefined);
      expect(getCachedUserInfo(userId)).to.equal(undefined);
    });

    it('getCachedUserInfo returns null for expired entries', () => {
      const clock = sinon.useFakeTimers(Date.now());
      setCachedUserInfo(userId, userRecord);
      clock.tick(USER_INFO_CACHE_TTL_MS + 1);
      expect(getCachedUserInfo(userId)).to.equal(null);
      expect(userInfoCache.has(userId)).to.equal(false);
      clock.restore();
    });
  });

  // -----------------------------------------------------------------------
  // Attachment helpers
  // -----------------------------------------------------------------------
  describe('attachment helpers', () => {
    describe('isSlackSupportedAttachment', () => {
      it('returns true for supported mimetypes (case insensitive)', () => {
        expect(isSlackSupportedAttachment({ id: '1', mimetype: 'image/jpeg' })).to.equal(true);
        expect(isSlackSupportedAttachment({ id: '2', mimetype: 'image/png' })).to.equal(true);
        expect(isSlackSupportedAttachment({ id: '3', mimetype: 'application/pdf' })).to.equal(true);
        expect(isSlackSupportedAttachment({ id: '4', mimetype: 'IMAGE/JPEG' })).to.equal(true);
      });

      it('returns false for unsupported mimetypes', () => {
        expect(isSlackSupportedAttachment({ id: '1', mimetype: 'application/json', name: 'a.json' })).to.equal(false);
        expect(isSlackSupportedAttachment({ id: '2', mimetype: 'application/zip' })).to.equal(false);
      });

      it('accepts each supported text type by extension', () => {
        for (const [ext, mime] of Object.entries(TEXT_ATTACHMENT_EXTENSION_MIMETYPES)) {
          expect(isSlackSupportedAttachment({ id: ext, name: `notes.${ext}`, mimetype: mime }), ext).to.equal(true);
          expect(isSlackSupportedAttachment({ id: ext, name: `notes.${ext}`, mimetype: 'text/plain' }), ext).to.equal(true);
          expect(isSlackSupportedAttachment({ id: ext, name: `NOTES.${ext.toUpperCase()}`, mimetype: 'application/octet-stream' }), ext).to.equal(true);
        }
      });

      it('rejects text-labelled files whose extension is not a supported text type', () => {
        expect(isSlackSupportedAttachment({ id: '1', name: 'script.py', mimetype: 'text/plain' })).to.equal(false);
        expect(isSlackSupportedAttachment({ id: '2', name: 'page.html', mimetype: 'text/html' })).to.equal(false);
      });

      it('returns false when mimetype is missing', () => {
        expect(isSlackSupportedAttachment({ id: '1' })).to.equal(false);
      });
    });

    describe('isSlackOversizedAttachment', () => {
      it('returns true when size exceeds limit', () => {
        expect(isSlackOversizedAttachment({ id: '1', size: MAX_ATTACHMENT_BYTES + 1 })).to.equal(true);
      });

      it('returns false when size is at or below limit', () => {
        expect(isSlackOversizedAttachment({ id: '1', size: MAX_ATTACHMENT_BYTES })).to.equal(false);
        expect(isSlackOversizedAttachment({ id: '1', size: 100 })).to.equal(false);
      });

      it('returns false when size is not a number', () => {
        expect(isSlackOversizedAttachment({ id: '1' })).to.equal(false);
      });
    });

    describe('classifySlackFiles', () => {
      it('classifies files into supported, unsupported, and oversized', () => {
        const files = [
          { id: '1', mimetype: 'image/png', size: 100, url_private_download: 'https://x' },
          { id: '2', mimetype: 'application/zip', size: 100, url_private: 'https://y' },
          { id: '3', mimetype: 'image/jpeg', size: MAX_ATTACHMENT_BYTES + 1, url_private_download: 'https://z' },
        ];
        const result = classifySlackFiles(files);
        expect(result.supported).to.have.length(1);
        expect(result.supported[0]!.id).to.equal('1');
        expect(result.unsupported).to.have.length(1);
        expect(result.unsupported[0]!.id).to.equal('2');
        expect(result.oversized).to.have.length(1);
        expect(result.oversized[0]!.id).to.equal('3');
      });

      it('marks supported-mimetype file as unsupported when it has no download URL', () => {
        const files = [{ id: '1', mimetype: 'image/png', size: 100 }];
        const result = classifySlackFiles(files);
        expect(result.supported).to.have.length(0);
        expect(result.unsupported).to.have.length(1);
      });

      it('returns empty arrays for undefined or non-array input', () => {
        expect(classifySlackFiles(undefined)).to.deep.equal({ supported: [], unsupported: [], oversized: [] });
        expect(classifySlackFiles(null as any)).to.deep.equal({ supported: [], unsupported: [], oversized: [] });
      });

      it('skips null/non-object entries in the array', () => {
        const result = classifySlackFiles([null, 42, 'string']);
        expect(result.supported).to.have.length(0);
        expect(result.unsupported).to.have.length(0);
      });
    });

    describe('extractSupportedAttachments', () => {
      it('returns only supported files', () => {
        const files = [
          { id: '1', mimetype: 'image/png', size: 100, url_private: 'https://x' },
          { id: '2', mimetype: 'application/zip', size: 100, url_private: 'https://y' },
        ];
        const result = extractSupportedAttachments(files);
        expect(result).to.have.length(1);
        expect(result[0]!.id).to.equal('1');
      });
    });

    describe('classifySlackFiles with text attachments', () => {
      it('marks an oversized Markdown file as oversized', () => {
        const result = classifySlackFiles([
          { id: '1', name: 'big.md', mimetype: 'text/plain', size: MAX_ATTACHMENT_BYTES + 1, url_private: 'https://x' },
        ]);
        expect(result.oversized.map((f) => f.id)).to.deep.equal(['1']);
        expect(result.supported).to.have.length(0);
      });
    });

    describe('resolveTextAttachmentMimetype', () => {
      it('maps each extension to the MIME type the upload endpoint parses', () => {
        expect(resolveTextAttachmentMimetype({ id: '1', name: 'a.md', mimetype: 'text/plain' })).to.equal('text/markdown');
        expect(resolveTextAttachmentMimetype({ id: '2', name: 'a.markdown', mimetype: 'text/x-markdown' })).to.equal('text/markdown');
        expect(resolveTextAttachmentMimetype({ id: '3', name: 'a.csv', mimetype: 'text/plain; charset=utf-8' })).to.equal('text/csv');
        expect(resolveTextAttachmentMimetype({ id: '4', name: 'a.tsv', mimetype: 'application/octet-stream' })).to.equal('text/tab-separated-values');
      });

      it('rejects a text extension on a file Slack reports as another type', () => {
        expect(resolveTextAttachmentMimetype({ id: '1', name: 'a.txt', mimetype: 'application/pdf' })).to.equal(null);
        expect(resolveTextAttachmentMimetype({ id: '2', name: 'a.md', mimetype: 'image/png' })).to.equal(null);
      });

      it('falls back to the MIME type or Slack filetype when the name has no extension', () => {
        expect(resolveTextAttachmentMimetype({ id: '1', name: 'README', mimetype: 'text/markdown' })).to.equal('text/markdown');
        expect(resolveTextAttachmentMimetype({ id: '2', mimetype: 'text/plain', filetype: 'markdown' })).to.equal('text/markdown');
        expect(resolveTextAttachmentMimetype({ id: '3', mimetype: 'text/plain', filetype: 'python' })).to.equal(null);
        expect(resolveTextAttachmentMimetype({ id: '4', name: 'LICENSE', mimetype: 'application/octet-stream' })).to.equal(null);
      });
    });

    describe('isReadableUtf8Text', () => {
      it('accepts UTF-8 text, including non-ASCII characters', () => {
        expect(isReadableUtf8Text(Buffer.from('# Notes\n\nCafé, naïve, 日本語 ✓', 'utf8'))).to.equal(true);
      });

      it('rejects invalid UTF-8 and binary content', () => {
        expect(isReadableUtf8Text(Buffer.from([0xff, 0xfe, 0xfd]))).to.equal(false);
        expect(isReadableUtf8Text(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).to.equal(false);
        expect(isReadableUtf8Text(Buffer.from('plain\u0000text', 'utf8'))).to.equal(false);
        expect(isReadableUtf8Text(Buffer.from('hi', 'utf16le'))).to.equal(false);
      });
    });

    describe('buildSkippedAttachmentsNotice', () => {
      it('names the unsupported file and lists every accepted type', () => {
        const notice = buildSkippedAttachmentsNotice(
          { unsupported: [{ id: '1', name: 'archive.zip', filetype: 'zip' }] },
          'none',
        );
        expect(notice).to.contain("I can't read this type of file: archive.zip (zip).");
        expect(notice).to.contain(SUPPORTED_ATTACHMENTS_HINT);
        for (const ext of Object.keys(TEXT_ATTACHMENT_EXTENSION_MIMETYPES)) {
          expect(SUPPORTED_ATTACHMENTS_HINT, ext).to.contain(`.${ext}`);
        }
        for (const label of ['PDF', 'JPEG', 'PNG', `${MAX_ATTACHMENT_MB} MB`]) {
          expect(SUPPORTED_ATTACHMENTS_HINT).to.contain(label);
        }
      });

      it('explains oversized and unreadable files and says the rest are still used', () => {
        const notice = buildSkippedAttachmentsNotice(
          {
            oversized: [{ id: '1', name: 'big.md' }],
            unreadable: [{ id: '2', name: 'a.txt' }, { id: '3', name: 'b.txt' }],
          },
          'otherAttachments',
        );
        expect(notice).to.contain(`This file is larger than the ${MAX_ATTACHMENT_MB} MB limit: big.md.`);
        expect(notice).to.contain("These files don't contain readable text: a.txt, b.txt.");
        expect(notice).to.contain('save them with UTF-8 encoding');
        expect(notice).to.contain("I'll answer using your other attachments.");
      });

      it('returns an empty string when nothing was skipped', () => {
        expect(buildSkippedAttachmentsNotice({}, 'none')).to.equal('');
      });

      it('says the message is still answered when only the attachments were skipped', () => {
        const notice = buildSkippedAttachmentsNotice({ unsupported: [{ id: '1', name: 'x.zip' }] }, 'messageOnly');
        expect(notice).to.contain("I'll answer your message without them.");
        expect(notice).not.to.contain('other attachments');
      });
    });

    describe('postSkippedAttachmentsNotice', () => {
      it('replies in the thread with the notice', async () => {
        const postMessage = sinon.stub().resolves({ ts: '2' });
        const client = { chat: { postMessage } } as any;
        await postSkippedAttachmentsNotice(
          client,
          { ts: '1', channel: 'D1' },
          { unsupported: [{ id: '1', name: 'x.exe' }] },
          'none',
        );
        expect(postMessage.calledOnce).to.equal(true);
        const args = postMessage.firstCall.args[0];
        expect(args.channel).to.equal('D1');
        expect(args.thread_ts).to.equal('1');
        expect(args.text).to.contain("I can't read this type of file: x.exe.");
      });
    });

    describe('messageHasQuestionText', () => {
      it('ignores the bot mention and whitespace', () => {
        expect(messageHasQuestionText('<@UBOT>', 'UBOT')).to.equal(false);
        expect(messageHasQuestionText('  <@UBOT>  \n', 'UBOT')).to.equal(false);
        expect(messageHasQuestionText(undefined, 'UBOT')).to.equal(false);
        expect(messageHasQuestionText('<@UBOT> summarise this', 'UBOT')).to.equal(true);
        expect(messageHasQuestionText('<@UOTHER>', 'UBOT')).to.equal(true);
        expect(messageHasQuestionText('hello')).to.equal(true);
      });
    });

    // The DM and app_mention handlers call handleIncomingAttachments the same
    // way whether or not the bot config has an agent, so these cover the
    // no-agent (default) setup that used to drop skipped files silently.
    describe('handleIncomingAttachments (DM and app_mention, no agent)', () => {
      const zip = { id: 'z', name: 'archive.zip', filetype: 'zip', mimetype: 'application/zip', size: 10, url_private: 'https://f/z' };
      const bigMd = { id: 'b', name: 'big.md', mimetype: 'text/plain', size: MAX_ATTACHMENT_BYTES + 1, url_private: 'https://f/b' };
      const notes = { id: 'n', name: 'notes.md', mimetype: 'text/plain', size: 10, url_private: 'https://f/n' };

      function client(): { client: any; postMessage: sinon.SinonStub } {
        const postMessage = sinon.stub().resolves({ ts: '9' });
        return { client: { chat: { postMessage } }, postMessage };
      }

      it('DM with only an unsupported file: the notice is the whole reply', async () => {
        const { client: c, postMessage } = client();
        const outcome = await handleIncomingAttachments(c, { ts: '1', channel: 'D1', text: '', files: [zip] }, 'UBOT');
        expect(outcome).to.deep.equal({ shouldAnswer: false, hasSupported: false });
        expect(postMessage.calledOnce).to.equal(true);
        const text: string = postMessage.firstCall.args[0].text;
        expect(text).to.contain("I can't read this type of file: archive.zip (zip).");
        expect(text).to.contain(SUPPORTED_ATTACHMENTS_HINT);
        expect(text).not.to.contain("I'll answer");
      });

      it('DM with text and an oversized file: posts the notice and still answers the text', async () => {
        const { client: c, postMessage } = client();
        const outcome = await handleIncomingAttachments(
          c,
          { ts: '1', channel: 'D1', text: 'what is our leave policy?', files: [bigMd] },
          'UBOT',
        );
        expect(outcome).to.deep.equal({ shouldAnswer: true, hasSupported: false });
        const text: string = postMessage.firstCall.args[0].text;
        expect(text).to.contain(`larger than the ${MAX_ATTACHMENT_MB} MB limit: big.md.`);
        expect(text).to.contain("I'll answer your message without them.");
      });

      it('app_mention with only the bot mention and an unsupported file: the notice is the reply, in the thread', async () => {
        const { client: c, postMessage } = client();
        const outcome = await handleIncomingAttachments(
          c,
          { ts: '5', thread_ts: '4', channel: 'C1', text: '<@UBOT>', files: [zip] },
          'UBOT',
        );
        expect(outcome.shouldAnswer).to.equal(false);
        expect(postMessage.firstCall.args[0]).to.include({ channel: 'C1', thread_ts: '4' });
      });

      it('app_mention with a question and a mix of files: notice, then answers with the accepted file', async () => {
        const { client: c, postMessage } = client();
        const outcome = await handleIncomingAttachments(
          c,
          { ts: '5', channel: 'C1', text: '<@UBOT> summarise these', files: [zip, notes] },
          'UBOT',
        );
        expect(outcome).to.deep.equal({ shouldAnswer: true, hasSupported: true });
        expect(postMessage.firstCall.args[0].text).to.contain("I'll answer using your other attachments.");
      });

      it('posts nothing and answers when every file is accepted or there are no files', async () => {
        const { client: c, postMessage } = client();
        expect(await handleIncomingAttachments(c, { ts: '1', channel: 'D1', files: [notes] }, 'UBOT'))
          .to.deep.equal({ shouldAnswer: true, hasSupported: true });
        expect(await handleIncomingAttachments(c, { ts: '1', channel: 'D1', text: 'hi' }, 'UBOT'))
          .to.deep.equal({ shouldAnswer: true, hasSupported: false });
        expect(postMessage.called).to.equal(false);
      });
    });

    describe('uploadSlackAttachments (mocked Slack download and backend upload)', () => {
      const utf8Markdown = Buffer.from('# Plan\n\n- Café ✓\n', 'utf8');

      function stubDownloads(bodies: Record<string, Buffer>): sinon.SinonStub {
        return sinon.stub(axios, 'get').callsFake(async (url: string) => {
          const body = bodies[url];
          if (!body) throw new Error(`unexpected download ${url}`);
          return { data: body } as any;
        });
      }

      function formParts(form: any): string {
        return form.getBuffer().toString('latin1');
      }

      it('uploads each accepted text type with the MIME type the backend parses', async () => {
        const entries = Object.entries(TEXT_ATTACHMENT_EXTENSION_MIMETYPES);
        const files = entries.map(([ext]) => ({
          id: ext,
          name: `notes.${ext}`,
          mimetype: 'text/plain',
          url_private_download: `https://files.slack.test/${ext}`,
        }));
        const getStub = stubDownloads(
          Object.fromEntries(files.map((f) => [f.url_private_download, utf8Markdown])),
        );
        const refs = files.map((f) => ({ recordId: f.id }));
        const postStub = sinon.stub(axios, 'post').resolves({ data: { attachments: refs } } as any);
        process.env.BACKEND_URL = 'http://backend.test';

        const result = await uploadSlackAttachments(files, 'xoxb-bot', 'access', 'agent-1');

        expect(getStub.callCount).to.equal(files.length);
        expect(getStub.firstCall.args[1]!.headers).to.deep.equal({ Authorization: 'Bearer xoxb-bot' });
        expect(postStub.calledOnce).to.equal(true);
        expect(postStub.firstCall.args[0]).to.equal(
          'http://backend.test/api/v1/agents/agent-1/conversations/internal/attachments/upload',
        );
        const body = formParts(postStub.firstCall.args[1]);
        for (const [ext, mime] of entries) {
          expect(body, ext).to.contain(`filename="notes.${ext}"\r\nContent-Type: ${mime}`);
        }
        expect(result).to.deep.equal({ attachments: refs, unreadable: [], oversized: [] });
      });

      it('skips a text file that is not valid UTF-8 and uploads the rest', async () => {
        const good = { id: 'g', name: 'good.md', mimetype: 'text/markdown', url_private: 'https://files.slack.test/g' };
        const bad = { id: 'b', name: 'bad.txt', mimetype: 'text/plain', url_private: 'https://files.slack.test/b' };
        stubDownloads({ [good.url_private]: utf8Markdown, [bad.url_private]: Buffer.from([0xc3, 0x28, 0xff]) });
        const postStub = sinon.stub(axios, 'post').resolves({ data: { attachments: [{ recordId: 'g' }] } } as any);

        const result = await uploadSlackAttachments([good, bad], 'xoxb-bot', 'access');

        expect(result.unreadable.map((f) => f.id)).to.deep.equal(['b']);
        const body = formParts(postStub.firstCall.args[1]);
        expect(body).to.contain('filename="good.md"');
        expect(body).not.to.contain('filename="bad.txt"');
      });

      it('rejects a download over the size limit even when Slack gave no size, and skips the upload call', async () => {
        const file = { id: 'o', name: 'huge.txt', mimetype: 'text/plain', url_private: 'https://files.slack.test/o' };
        stubDownloads({ [file.url_private]: Buffer.alloc(MAX_ATTACHMENT_BYTES + 1, 0x61) });
        const postStub = sinon.stub(axios, 'post');

        const result = await uploadSlackAttachments([file], 'xoxb-bot', 'access');

        expect(result).to.deep.equal({ attachments: [], unreadable: [], oversized: [file] });
        expect(postStub.called).to.equal(false);
      });

      it('keeps uploading images and PDFs with their own MIME type', async () => {
        const pdf = { id: 'p', name: 'doc.pdf', mimetype: 'application/pdf', url_private: 'https://files.slack.test/p' };
        stubDownloads({ [pdf.url_private]: Buffer.from([0x25, 0x50, 0x44, 0x46, 0xff]) });
        const postStub = sinon.stub(axios, 'post').resolves({ data: { attachments: [{ recordId: 'p' }] } } as any);

        const result = await uploadSlackAttachments([pdf], 'xoxb-bot', 'access');

        expect(result.attachments).to.have.length(1);
        expect(formParts(postStub.firstCall.args[1])).to.contain('filename="doc.pdf"\r\nContent-Type: application/pdf');
      });
    });
  });

  // -----------------------------------------------------------------------
  // SSE parsing
  // -----------------------------------------------------------------------
  describe('parseSSEEvents', () => {
    it('parses a single SSE event with JSON data', () => {
      const buffer = 'event: message\ndata: {"text":"hello"}\n\n';
      const { events, remainder } = parseSSEEvents(buffer);
      expect(events).to.have.length(1);
      expect(events[0]!.event).to.equal('message');
      expect(events[0]!.data).to.deep.equal({ text: 'hello' });
      expect(remainder).to.equal('');
    });

    it('parses multiple events', () => {
      const buffer = 'event: start\ndata: {}\n\nevent: end\ndata: done\n\n';
      const { events } = parseSSEEvents(buffer);
      expect(events).to.have.length(2);
      expect(events[0]!.event).to.equal('start');
      expect(events[1]!.event).to.equal('end');
      expect(events[1]!.data).to.equal('done');
    });

    it('returns incomplete event as remainder', () => {
      const buffer = 'event: partial\ndata: {"x":1}';
      const { events, remainder } = parseSSEEvents(buffer);
      expect(events).to.have.length(0);
      expect(remainder).to.equal('event: partial\ndata: {"x":1}');
    });

    it('defaults event type to "message"', () => {
      const buffer = 'data: hello\n\n';
      const { events } = parseSSEEvents(buffer);
      expect(events[0]!.event).to.equal('message');
      expect(events[0]!.data).to.equal('hello');
    });

    it('skips blank raw events', () => {
      const buffer = '\n\ndata: real\n\n';
      const { events } = parseSSEEvents(buffer);
      expect(events).to.have.length(1);
      expect(events[0]!.data).to.equal('real');
    });

    it('keeps non-JSON data as string', () => {
      const buffer = 'data: not json\n\n';
      const { events } = parseSSEEvents(buffer);
      expect(events[0]!.data).to.equal('not json');
    });
  });

  // -----------------------------------------------------------------------
  // Error handling
  // -----------------------------------------------------------------------
  describe('error handling', () => {
    describe('readMessageFromObject', () => {
      it('returns null for non-object values', () => {
        expect(readMessageFromObject(null)).to.equal(null);
        expect(readMessageFromObject(undefined)).to.equal(null);
        expect(readMessageFromObject('string')).to.equal(null);
        expect(readMessageFromObject(42)).to.equal(null);
      });

      it('extracts message from "message" key', () => {
        expect(readMessageFromObject({ message: 'hello' })).to.equal('hello');
      });

      it('extracts from "error", "detail", "reason", "msg" keys in priority order', () => {
        expect(readMessageFromObject({ error: 'err' })).to.equal('err');
        expect(readMessageFromObject({ detail: 'det' })).to.equal('det');
        expect(readMessageFromObject({ reason: 'rsn' })).to.equal('rsn');
        expect(readMessageFromObject({ msg: 'msg' })).to.equal('msg');
      });

      it('prefers "message" over "error"', () => {
        expect(readMessageFromObject({ message: 'primary', error: 'secondary' })).to.equal('primary');
      });

      it('reads nested error.message', () => {
        expect(readMessageFromObject({ error: { message: 'nested' } })).to.equal('nested');
      });

      it('skips blank string values', () => {
        expect(readMessageFromObject({ message: '  ', error: 'real' })).to.equal('real');
      });

      it('returns null when no recognized key has a string value', () => {
        expect(readMessageFromObject({ foo: 'bar' })).to.equal(null);
      });
    });

    describe('readMessageFromTextPayload', () => {
      it('returns null for blank or empty input', () => {
        expect(readMessageFromTextPayload('')).to.equal(null);
        expect(readMessageFromTextPayload('   ')).to.equal(null);
      });

      it('parses JSON payload and extracts message', () => {
        expect(readMessageFromTextPayload('{"message":"parsed"}')).to.equal('parsed');
      });

      it('returns plain text when it is not JSON or SSE', () => {
        expect(readMessageFromTextPayload('plain text')).to.equal('plain text');
      });

      it('extracts error from SSE event payload', () => {
        const ssePayload = 'event: error\ndata: {"message":"stream error"}\n\n';
        expect(readMessageFromTextPayload(ssePayload)).to.equal('stream error');
      });

      it('returns raw text for SSE without error event when JSON parse fails', () => {
        const ssePayload = 'event: message\ndata: {"message":"not an error"}\n\n';
        const result = readMessageFromTextPayload(ssePayload);
        expect(result).to.equal(ssePayload.trim());
      });
    });

    describe('extractSlackErrorMessage', () => {
      it('returns default for falsy input', () => {
        expect(extractSlackErrorMessage(null)).to.equal(DEFAULT_SLACK_ERROR_MESSAGE);
        expect(extractSlackErrorMessage(undefined)).to.equal(DEFAULT_SLACK_ERROR_MESSAGE);
      });

      it('returns trimmed string errors', () => {
        expect(extractSlackErrorMessage('  oops  ')).to.equal('oops');
      });

      it('extracts message from Error instances', () => {
        expect(extractSlackErrorMessage(new Error('err msg'))).to.equal('err msg');
      });

      it('extracts message from plain objects', () => {
        expect(extractSlackErrorMessage({ message: 'obj msg' })).to.equal('obj msg');
      });

      it('returns default when object has no recognized message key', () => {
        expect(extractSlackErrorMessage({ foo: 'bar' })).to.equal(DEFAULT_SLACK_ERROR_MESSAGE);
      });
    });

    describe('normalizeSlackErrorMessage', () => {
      it('collapses multiple whitespace into single space', () => {
        expect(normalizeSlackErrorMessage('  hello   world  ')).to.equal('hello world');
      });

      it('trims leading/trailing whitespace', () => {
        expect(normalizeSlackErrorMessage('  msg  ')).to.equal('msg');
      });
    });

    describe('formatSlackErrorMessage', () => {
      it('returns default for empty normalized message', () => {
        expect(formatSlackErrorMessage('   ')).to.equal(DEFAULT_SLACK_ERROR_MESSAGE);
      });

      it('truncates long messages', () => {
        const longMsg = 'x'.repeat(MAX_USER_VISIBLE_ERROR_LENGTH + 100);
        const result = formatSlackErrorMessage(longMsg);
        expect(result.length).to.equal(MAX_USER_VISIBLE_ERROR_LENGTH);
        expect(result.endsWith('...')).to.equal(true);
      });

      it('returns short messages unchanged', () => {
        expect(formatSlackErrorMessage('short')).to.equal('short');
      });
    });

    describe('isReadableStreamLike', () => {
      it('returns true for objects with an "on" function', () => {
        expect(isReadableStreamLike({ on: () => {} })).to.equal(true);
      });

      it('returns false for non-objects and objects without "on"', () => {
        expect(isReadableStreamLike(null)).to.equal(false);
        expect(isReadableStreamLike('str')).to.equal(false);
        expect(isReadableStreamLike({ on: 'not a function' })).to.equal(false);
      });
    });
  });

  // -----------------------------------------------------------------------
  // Text helpers
  // -----------------------------------------------------------------------
  describe('text helpers', () => {
    describe('truncateForSlack', () => {
      it('returns text unchanged when within limit', () => {
        expect(truncateForSlack('short')).to.equal('short');
      });

      it('truncates from the beginning and prepends "..."', () => {
        const longText = 'a'.repeat(SLACK_MAX_TEXT_LENGTH + 10);
        const result = truncateForSlack(longText);
        expect(result.length).to.equal(SLACK_MAX_TEXT_LENGTH);
        expect(result.startsWith('...')).to.equal(true);
      });
    });

    describe('truncateForSlackStreamMarkdown', () => {
      it('truncates to SLACK_STREAM_MARKDOWN_LIMIT', () => {
        const longText = 'b'.repeat(SLACK_STREAM_MARKDOWN_LIMIT + 10);
        const result = truncateForSlackStreamMarkdown(longText);
        expect(result.length).to.equal(SLACK_STREAM_MARKDOWN_LIMIT);
        expect(result.startsWith('...')).to.equal(true);
      });
    });

    describe('truncateFromEnd', () => {
      it('returns text unchanged when within limit', () => {
        expect(truncateFromEnd('hello', 10)).to.equal('hello');
      });

      it('truncates with "..." suffix', () => {
        expect(truncateFromEnd('hello world', 8)).to.equal('hello...');
      });

      it('returns empty string when limit is 0', () => {
        expect(truncateFromEnd('text', 0)).to.equal('');
      });

      it('returns raw slice when limit <= 3', () => {
        expect(truncateFromEnd('hello', 3)).to.equal('hel');
        expect(truncateFromEnd('hello', 1)).to.equal('h');
      });
    });

    describe('resolveThreadId', () => {
      it('returns thread_ts when present', () => {
        const msg = { thread_ts: 'thread-1', ts: 'msg-1' } as SlackMessagePayload;
        expect(resolveThreadId(msg)).to.equal('thread-1');
      });

      it('falls back to ts when thread_ts is absent', () => {
        const msg = { ts: 'msg-1' } as SlackMessagePayload;
        expect(resolveThreadId(msg)).to.equal('msg-1');
      });
    });

    describe('splitByLengthPreferringNewlines', () => {
      it('returns empty array for empty text', () => {
        expect(splitByLengthPreferringNewlines('', 10)).to.deep.equal([]);
      });

      it('returns whole text when within limit', () => {
        expect(splitByLengthPreferringNewlines('hello', 10)).to.deep.equal(['hello']);
      });

      it('splits at newline when possible', () => {
        const text = 'aaa\nbbb\nccc';
        const result = splitByLengthPreferringNewlines(text, 5);
        expect(result[0]).to.equal('aaa\n');
        expect(result[1]).to.equal('bbb\n');
        expect(result[2]).to.equal('ccc');
      });

      it('hard-splits when no newline fits within limit', () => {
        const text = 'abcdefghij';
        const result = splitByLengthPreferringNewlines(text, 4);
        expect(result).to.deep.equal(['abcd', 'efgh', 'ij']);
      });

      it('returns text as-is in one-element array when limit is 0', () => {
        expect(splitByLengthPreferringNewlines('hello', 0)).to.deep.equal(['hello']);
      });
    });

    describe('describeSlackFile', () => {
      it('returns "name (ext)" when both are present', () => {
        expect(describeSlackFile({ id: '1', name: 'file.txt', filetype: 'txt' })).to.equal('file.txt (txt)');
      });

      it('returns name without extension when filetype is missing', () => {
        expect(describeSlackFile({ id: '1', name: 'file.txt' })).to.equal('file.txt');
      });

      it('falls back to file id when name is missing', () => {
        expect(describeSlackFile({ id: 'F123', filetype: 'pdf' })).to.equal('F123 (pdf)');
      });

      it('falls back to file id with no extension', () => {
        expect(describeSlackFile({ id: 'F123' })).to.equal('F123');
      });
    });
  });

  // -----------------------------------------------------------------------
  // Markdown table detection
  // -----------------------------------------------------------------------
  describe('markdown table detection', () => {
    describe('isMarkdownTableRow', () => {
      it('returns true for pipe-delimited rows', () => {
        expect(isMarkdownTableRow('| a | b |')).to.equal(true);
        expect(isMarkdownTableRow('  | a | b |  ')).to.equal(true);
      });

      it('returns false for lines without enclosing pipes', () => {
        expect(isMarkdownTableRow('no pipes')).to.equal(false);
        expect(isMarkdownTableRow('| no close')).to.equal(false);
      });
    });

    describe('isMarkdownTableSeparatorRow', () => {
      it('returns true for separator rows with dashes', () => {
        expect(isMarkdownTableSeparatorRow('| --- | --- |')).to.equal(true);
        expect(isMarkdownTableSeparatorRow('| :---: | ---: |')).to.equal(true);
      });

      it('returns false for rows without enough dashes', () => {
        expect(isMarkdownTableSeparatorRow('| -- | -- |')).to.equal(false);
        expect(isMarkdownTableSeparatorRow('| abc | def |')).to.equal(false);
      });
    });

    describe('parseMarkdownTableCells', () => {
      it('splits and trims cells', () => {
        expect(parseMarkdownTableCells('| a | b | c |')).to.deep.equal(['a', 'b', 'c']);
      });

      it('handles extra whitespace', () => {
        expect(parseMarkdownTableCells('|  foo  |  bar  |')).to.deep.equal(['foo', 'bar']);
      });
    });

    describe('getFenceMarker', () => {
      it('detects backtick fence', () => {
        expect(getFenceMarker('```javascript')).to.equal('`');
        expect(getFenceMarker('````')).to.equal('`');
      });

      it('detects tilde fence', () => {
        expect(getFenceMarker('~~~')).to.equal('~');
      });

      it('returns null for non-fence lines', () => {
        expect(getFenceMarker('normal text')).to.equal(null);
        expect(getFenceMarker('``not enough')).to.equal(null);
      });
    });

    describe('isFenceClosingLine', () => {
      it('detects backtick closing for backtick marker', () => {
        expect(isFenceClosingLine('```', '`')).to.equal(true);
      });

      it('detects tilde closing for tilde marker', () => {
        expect(isFenceClosingLine('~~~', '~')).to.equal(true);
      });

      it('does not cross-match markers', () => {
        expect(isFenceClosingLine('~~~', '`')).to.equal(false);
      });
    });

    describe('tryParseMarkdownTableAtLine', () => {
      it('parses a valid table', () => {
        const lines = ['| H1 | H2 |', '| --- | --- |', '| a | b |', '| c | d |'];
        const result = tryParseMarkdownTableAtLine(lines, 0);
        expect(result).to.not.equal(null);
        expect(result!.header).to.deep.equal(['H1', 'H2']);
        expect(result!.rows).to.have.length(2);
        expect(result!.nextLineIndex).to.equal(4);
      });

      it('returns null when header/separator/data is missing', () => {
        expect(tryParseMarkdownTableAtLine(['| H |', '| --- |'], 0)).to.equal(null);
        expect(tryParseMarkdownTableAtLine(['| H |', 'not sep', '| d |'], 0)).to.equal(null);
      });

      it('stops parsing at non-table rows', () => {
        const lines = ['| H |', '| --- |', '| d |', 'text', '| e |'];
        const result = tryParseMarkdownTableAtLine(lines, 0);
        expect(result!.rows).to.have.length(1);
        expect(result!.nextLineIndex).to.equal(3);
      });
    });

    describe('hasMarkdownTableStartOutsideCodeFences', () => {
      it('returns true for content with a table', () => {
        const content = 'text\n| H |\n| --- |\n| d |';
        expect(hasMarkdownTableStartOutsideCodeFences(content)).to.equal(true);
      });

      it('returns false when table is inside a code fence', () => {
        const content = '```\n| H |\n| --- |\n| d |\n```';
        expect(hasMarkdownTableStartOutsideCodeFences(content)).to.equal(false);
      });

      it('returns false for empty content', () => {
        expect(hasMarkdownTableStartOutsideCodeFences('')).to.equal(false);
      });

      it('returns false for no table in content', () => {
        expect(hasMarkdownTableStartOutsideCodeFences('just text')).to.equal(false);
      });

      it('detects table after a closed code fence', () => {
        const content = '```\ncode\n```\n| H |\n| --- |\n| d |';
        expect(hasMarkdownTableStartOutsideCodeFences(content)).to.equal(true);
      });
    });

    describe('splitMarkdownMessageIntoTableAwareSegments', () => {
      it('returns empty array for empty content', () => {
        expect(splitMarkdownMessageIntoTableAwareSegments('')).to.deep.equal([]);
      });

      it('returns single markdown segment for text without tables', () => {
        const result = splitMarkdownMessageIntoTableAwareSegments('hello world');
        expect(result).to.have.length(1);
        expect(result[0]!.type).to.equal('markdown');
      });

      it('splits markdown and table segments', () => {
        const content = 'before\n| H1 | H2 |\n| --- | --- |\n| a | b |\nafter';
        const result = splitMarkdownMessageIntoTableAwareSegments(content);
        expect(result.length).to.be.greaterThanOrEqual(2);
        const tableSegment = result.find((s: { type: string }) => s.type === 'table');
        expect(tableSegment).to.not.equal(undefined);
        if (tableSegment && tableSegment.type === 'table') {
          expect(tableSegment.header).to.deep.equal(['H1', 'H2']);
          expect(tableSegment.rows).to.have.length(1);
        }
      });

      it('does not extract tables inside code fences', () => {
        const content = '```\n| H |\n| --- |\n| d |\n```';
        const result = splitMarkdownMessageIntoTableAwareSegments(content);
        expect(result.every((s: { type: string }) => s.type === 'markdown')).to.equal(true);
      });
    });
  });

  // -----------------------------------------------------------------------
  // URL / link helpers
  // -----------------------------------------------------------------------
  describe('URL / link helpers', () => {
    describe('splitTrailingPunctuationFromUrl', () => {
      it('strips trailing punctuation', () => {
        expect(splitTrailingPunctuationFromUrl('https://example.com).')).to.deep.equal({
          url: 'https://example.com',
          trailingText: ').',
        });
      });

      it('returns url unchanged when no trailing punctuation', () => {
        expect(splitTrailingPunctuationFromUrl('https://example.com/path')).to.deep.equal({
          url: 'https://example.com/path',
          trailingText: '',
        });
      });

      it('strips multiple trailing punctuation characters', () => {
        expect(splitTrailingPunctuationFromUrl('https://x.com?!,')).to.deep.equal({
          url: 'https://x.com',
          trailingText: '?!,',
        });
      });
    });

    describe('buildFrontendRecordUrl', () => {
      it('uses FRONTEND_PUBLIC_URL env var', () => {
        process.env.FRONTEND_PUBLIC_URL = 'https://app.example.com';
        expect(buildFrontendRecordUrl('rec123')).to.equal('https://app.example.com/record/rec123');
      });

      it('defaults to localhost when env var is not set', () => {
        expect(buildFrontendRecordUrl('rec123')).to.equal('http://localhost:3000/record/rec123');
      });

      it('encodes special characters in recordId', () => {
        expect(buildFrontendRecordUrl('a/b c')).to.include(encodeURIComponent('a/b c'));
      });
    });

    describe('resolveSlackArtifactLink', () => {
      it('uses recordId when available', () => {
        const result = resolveSlackArtifactLink({ recordId: 'r1' });
        expect(result).to.include('/record/r1');
      });

      it('falls back to downloadUrl for absolute http URLs', () => {
        expect(resolveSlackArtifactLink({ downloadUrl: 'https://cdn.example.com/file' })).to.equal(
          'https://cdn.example.com/file',
        );
      });

      it('prepends frontend base for relative downloadUrl', () => {
        expect(resolveSlackArtifactLink({ downloadUrl: '/api/download/123' })).to.include('/api/download/123');
      });

      it('returns null for record: scheme downloadUrl', () => {
        expect(resolveSlackArtifactLink({ downloadUrl: 'record:abc' })).to.equal(null);
      });

      it('returns null when both are empty', () => {
        expect(resolveSlackArtifactLink({})).to.equal(null);
        expect(resolveSlackArtifactLink({ recordId: '', downloadUrl: '' })).to.equal(null);
      });
    });

    describe('buildChatStreamUrl', () => {
      beforeEach(() => {
        process.env.BACKEND_URL = 'https://api.example.com';
      });

      it('builds URL without agent or conversation', () => {
        expect(buildChatStreamUrl(null, null)).to.equal(
          'https://api.example.com/api/v1/conversations/internal/stream',
        );
      });

      it('builds URL with conversationId only', () => {
        expect(buildChatStreamUrl('conv1', null)).to.equal(
          'https://api.example.com/api/v1/conversations/internal/conv1/messages/stream',
        );
      });

      it('builds URL with agentId only', () => {
        expect(buildChatStreamUrl(null, 'agent1')).to.equal(
          'https://api.example.com/api/v1/agents/agent1/conversations/internal/stream',
        );
      });

      it('builds URL with both agentId and conversationId', () => {
        expect(buildChatStreamUrl('conv1', 'agent1')).to.equal(
          'https://api.example.com/api/v1/agents/agent1/conversations/internal/conv1/messages/stream',
        );
      });

      it('encodes agentId', () => {
        const url = buildChatStreamUrl(null, 'a/b');
        expect(url).to.include(encodeURIComponent('a/b'));
      });
    });
  });

  // -----------------------------------------------------------------------
  // Rich text builders
  // -----------------------------------------------------------------------
  describe('rich text builders', () => {
    describe('buildSlackRichTextTextElement', () => {
      it('creates a text element', () => {
        const el = buildSlackRichTextTextElement('hello', false);
        expect(el.type).to.equal('text');
        expect(el.text).to.equal('hello');
        expect(el.style).to.equal(undefined);
      });

      it('adds bold style when makeBold is true', () => {
        const el = buildSlackRichTextTextElement('bold text', true);
        expect(el.style).to.deep.equal({ bold: true });
      });
    });

    describe('buildSlackRichTextLinkElement', () => {
      it('creates a link element with label', () => {
        const el = buildSlackRichTextLinkElement('https://x.com', 'Link', false);
        expect(el.type).to.equal('link');
        expect(el.url).to.equal('https://x.com');
        expect(el.text).to.equal('Link');
      });

      it('omits text when label equals url', () => {
        const el = buildSlackRichTextLinkElement('https://x.com', 'https://x.com', false);
        expect(el.text).to.equal(undefined);
      });

      it('omits text when label is undefined', () => {
        const el = buildSlackRichTextLinkElement('https://x.com', undefined, false);
        expect(el.text).to.equal(undefined);
      });

      it('applies bold style', () => {
        const el = buildSlackRichTextLinkElement('https://x.com', 'Link', true);
        expect(el.style).to.deep.equal({ bold: true });
      });
    });

    describe('buildSlackTableCellElements', () => {
      it('creates text element for plain text', () => {
        const elements = buildSlackTableCellElements('plain', false);
        expect(elements.length).to.be.greaterThanOrEqual(1);
        expect(elements[0]!.type).to.equal('text');
      });

      it('creates link element for markdown links', () => {
        const elements = buildSlackTableCellElements('[Google](https://google.com)', false);
        const link = elements.find((e: Record<string, unknown>) => e.type === 'link');
        expect(link).to.not.equal(undefined);
        expect(link!.url).to.equal('https://google.com');
        expect(link!.text).to.equal('Google');
      });

      it('handles bare URLs', () => {
        const elements = buildSlackTableCellElements('Visit https://example.com today', false);
        const link = elements.find((e: Record<string, unknown>) => e.type === 'link');
        expect(link).to.not.equal(undefined);
        expect(link!.url).to.equal('https://example.com');
      });

      it('returns space element for empty cell', () => {
        const elements = buildSlackTableCellElements('', false);
        expect(elements).to.have.length(1);
        expect(elements[0]!.text).to.equal(' ');
      });

      it('makes elements bold for header cells', () => {
        const elements = buildSlackTableCellElements('header', true);
        expect(elements[0]!.style).to.deep.equal({ bold: true });
      });
    });

    describe('buildSlackTableBlock', () => {
      it('builds a table block with normalized column count', () => {
        const rows = [
          ['H1', 'H2', 'H3'],
          ['a', 'b'],
        ];
        const block = buildSlackTableBlock(rows);
        expect(block.type).to.equal('table');
        expect((block.rows as any[][])[0]).to.have.length(3);
        expect((block.rows as any[][])[1]).to.have.length(3);
      });

      it('caps columns at MAX_TABLE_COLS', () => {
        const wideRow = Array.from({ length: MAX_TABLE_COLS + 5 }, (_, i) => `col${i}`);
        const block = buildSlackTableBlock([wideRow]);
        expect((block.rows as any[][])[0]).to.have.length(MAX_TABLE_COLS);
      });
    });
  });

  // -----------------------------------------------------------------------
  // Block splitting
  // -----------------------------------------------------------------------
  describe('block splitting', () => {
    describe('splitSectionTextObjectByLimit', () => {
      it('returns empty for non-object input', () => {
        const result = splitSectionTextObjectByLimit(null, 100);
        expect(result.chunks).to.have.length(0);
        expect(result.didSplit).to.equal(false);
        expect(result.hasTextObject).to.equal(false);
      });

      it('does not split when text fits within limit', () => {
        const result = splitSectionTextObjectByLimit({ type: 'mrkdwn', text: 'short' }, 100);
        expect(result.chunks).to.have.length(1);
        expect(result.didSplit).to.equal(false);
        expect(result.hasTextObject).to.equal(true);
      });

      it('splits long text', () => {
        const longText = 'a'.repeat(200);
        const result = splitSectionTextObjectByLimit({ type: 'mrkdwn', text: longText }, 80);
        expect(result.chunks.length).to.be.greaterThan(1);
        expect(result.didSplit).to.equal(true);
      });

      it('returns single chunk when text property is not a string', () => {
        const result = splitSectionTextObjectByLimit({ type: 'mrkdwn', text: 42 }, 100);
        expect(result.chunks).to.have.length(1);
        expect(result.didSplit).to.equal(false);
      });
    });

    describe('splitSectionFieldsByLimit', () => {
      it('returns empty for non-array input', () => {
        const result = splitSectionFieldsByLimit('not an array');
        expect(result.groups).to.have.length(0);
        expect(result.hasFieldsArray).to.equal(false);
      });

      it('groups fields by SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT', () => {
        const fields = Array.from({ length: SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT + 2 }, (_, i) => ({
          type: 'mrkdwn',
          text: `field ${i}`,
        }));
        const result = splitSectionFieldsByLimit(fields);
        expect(result.groups).to.have.length(2);
        expect(result.didSplit).to.equal(true);
      });

      it('splits long field text', () => {
        const fields = [{ type: 'mrkdwn', text: 'x'.repeat(SLACK_SECTION_FIELD_TEXT_LIMIT + 100) }];
        const result = splitSectionFieldsByLimit(fields);
        expect(result.didSplit).to.equal(true);
      });
    });

    describe('splitSectionBlockForSlackLimits', () => {
      it('returns non-section blocks unchanged', () => {
        const block = { type: 'divider' };
        expect(splitSectionBlockForSlackLimits(block)).to.deep.equal([block]);
      });

      it('returns section block unchanged when within limits', () => {
        const block = { type: 'section', text: { type: 'mrkdwn', text: 'short' } };
        const result = splitSectionBlockForSlackLimits(block);
        expect(result).to.have.length(1);
      });

      it('splits section with oversized text', () => {
        const block = {
          type: 'section',
          text: { type: 'mrkdwn', text: 'a'.repeat(SLACK_SECTION_TEXT_LIMIT + 500) },
        };
        const result = splitSectionBlockForSlackLimits(block);
        expect(result.length).to.be.greaterThan(1);
      });
    });

    describe('normalizeSlackBlocksForLimits', () => {
      it('normalizes all blocks in array', () => {
        const blocks = [
          { type: 'divider' },
          { type: 'section', text: { type: 'mrkdwn', text: 'ok' } },
        ];
        const result = normalizeSlackBlocksForLimits(blocks);
        expect(result).to.have.length(2);
      });
    });

    describe('getBlockPayloadTextSize', () => {
      it('returns 0 for non-objects', () => {
        expect(getBlockPayloadTextSize(null)).to.equal(0);
        expect(getBlockPayloadTextSize(undefined)).to.equal(0);
      });

      it('sums section text and field sizes', () => {
        const block = {
          type: 'section',
          text: { type: 'mrkdwn', text: 'hello' },
          fields: [{ text: 'f1' }, { text: 'f2' }],
        };
        expect(getBlockPayloadTextSize(block)).to.equal(5 + 2 + 2);
      });

      it('calculates rich_text block size', () => {
        const block = {
          type: 'rich_text',
          elements: [
            {
              type: 'rich_text_section',
              elements: [
                { type: 'text', text: 'hello' },
                { type: 'link', url: 'https://x.com' },
              ],
            },
          ],
        };
        expect(getBlockPayloadTextSize(block)).to.equal(5 + 'https://x.com'.length);
      });

      it('falls back to JSON.stringify length for unknown block types', () => {
        const block = { type: 'unknown', data: 'test' };
        expect(getBlockPayloadTextSize(block)).to.equal(JSON.stringify(block).length);
      });
    });

    describe('splitSlackBlocksByLimit', () => {
      it('returns empty array for empty blocks', () => {
        expect(splitSlackBlocksByLimit([])).to.deep.equal([]);
      });

      it('splits by count limit', () => {
        const blocks = Array.from({ length: 5 }, () => ({ type: 'divider' }));
        const result = splitSlackBlocksByLimit(blocks, 2);
        expect(result).to.have.length(3);
        expect(result[0]).to.have.length(2);
        expect(result[1]).to.have.length(2);
        expect(result[2]).to.have.length(1);
      });

      it('splits by text size limit', () => {
        const blocks = [
          { type: 'section', text: { type: 'mrkdwn', text: 'a'.repeat(6000) } },
          { type: 'section', text: { type: 'mrkdwn', text: 'b'.repeat(6000) } },
        ];
        const result = splitSlackBlocksByLimit(blocks, 50, 10000);
        expect(result).to.have.length(2);
      });

      it('separates consecutive table blocks', () => {
        const blocks = [
          { type: 'table', rows: [] },
          { type: 'table', rows: [] },
        ];
        const result = splitSlackBlocksByLimit(blocks, 50, 100000);
        expect(result).to.have.length(2);
      });
    });
  });

  // -----------------------------------------------------------------------
  // User / thread helpers
  // -----------------------------------------------------------------------
  describe('user / thread helpers', () => {
    describe('isThreadFollowUpMessage', () => {
      it('returns true when thread_ts differs from ts', () => {
        expect(isThreadFollowUpMessage({ thread_ts: 't1', ts: 't2' } as SlackMessagePayload)).to.equal(true);
      });

      it('returns false when thread_ts equals ts (thread root)', () => {
        expect(isThreadFollowUpMessage({ thread_ts: 't1', ts: 't1' } as SlackMessagePayload)).to.equal(false);
      });

      it('returns false when thread_ts is absent', () => {
        expect(isThreadFollowUpMessage({ ts: 't1' } as SlackMessagePayload)).to.equal(false);
      });
    });

    describe('sanitizeSlackLabelValue', () => {
      it('collapses whitespace and trims', () => {
        expect(sanitizeSlackLabelValue('  hello   world  ')).to.equal('hello world');
      });

      it('returns empty string for undefined', () => {
        expect(sanitizeSlackLabelValue(undefined)).to.equal('');
      });
    });

    describe('pickSlackDisplayName', () => {
      it('picks display_name first', () => {
        const user: SlackUserRecord = {
          profile: { display_name: 'Display', real_name: 'Real' },
          real_name: 'Top Real',
          name: 'username',
        };
        expect(pickSlackDisplayName(user)).to.equal('Display');
      });

      it('falls back to real_name when display_name is empty', () => {
        const user: SlackUserRecord = {
          profile: { display_name: '' },
          real_name: 'Real',
          name: 'username',
        };
        expect(pickSlackDisplayName(user)).to.equal('Real');
      });

      it('returns empty string for undefined userRecord', () => {
        expect(pickSlackDisplayName(undefined)).to.equal('');
      });
    });

    describe('formatMentionedUser', () => {
      it('formats user with email and display name', () => {
        const user: SlackUserRecord = {
          profile: { email: 'test@example.com', display_name: 'Test User' },
        };
        expect(formatMentionedUser(user, 'U123')).to.equal(
          'Test User (Email: test@example.com, Slack user id: U123)',
        );
      });

      it('uses "User" as fallback display name', () => {
        const user: SlackUserRecord = { profile: { email: 'test@example.com' } };
        expect(formatMentionedUser(user, 'U123')).to.equal(
          'User (Email: test@example.com, Slack user id: U123)',
        );
      });

      it('returns empty string when email is missing', () => {
        const user: SlackUserRecord = { profile: { display_name: 'Test' } };
        expect(formatMentionedUser(user, 'U123')).to.equal('');
      });
    });

    describe('formatSlackUserLabel', () => {
      it('returns "name (email)" when both available', () => {
        const user: SlackUserRecord = {
          profile: { email: 'u@ex.com', display_name: 'User' },
        };
        expect(formatSlackUserLabel(user, 'U1')).to.equal('User (u@ex.com)');
      });

      it('returns just name when email is missing', () => {
        const user: SlackUserRecord = { profile: { display_name: 'User' } };
        expect(formatSlackUserLabel(user, 'U1')).to.equal('User');
      });

      it('returns just email when name is missing', () => {
        const user: SlackUserRecord = { profile: { email: 'u@ex.com' } };
        expect(formatSlackUserLabel(user, 'U1')).to.equal('u@ex.com');
      });

      it('returns "User (id)" as fallback', () => {
        expect(formatSlackUserLabel(undefined, 'U1')).to.equal('User (U1)');
      });
    });

    describe('slackCallerDisplayName', () => {
      it('returns display name', () => {
        expect(slackCallerDisplayName({ profile: { display_name: 'Test' } })).to.equal('Test');
      });

      it('returns empty for undefined', () => {
        expect(slackCallerDisplayName(undefined)).to.equal('');
      });
    });

    describe('inferThreadMessageSpeaker', () => {
      const labels = new Map([['U1', 'Alice']]);

      it('returns "Assistant" for bot messages', () => {
        expect(inferThreadMessageSpeaker({ bot_id: 'B1', ts: '1' } as SlackMessagePayload, labels)).to.equal(
          'Assistant',
        );
        expect(
          inferThreadMessageSpeaker({ subtype: 'bot_message', ts: '1' } as SlackMessagePayload, labels),
        ).to.equal('Assistant');
      });

      it('returns user label when available', () => {
        expect(inferThreadMessageSpeaker({ user: 'U1', ts: '1' } as SlackMessagePayload, labels)).to.equal('Alice');
      });

      it('returns fallback for unknown user', () => {
        expect(inferThreadMessageSpeaker({ user: 'U99', ts: '1' } as SlackMessagePayload, labels)).to.equal(
          'User (U99)',
        );
      });

      it('returns "User" when no user or bot_id', () => {
        expect(inferThreadMessageSpeaker({ ts: '1' } as SlackMessagePayload, labels)).to.equal('User');
      });
    });
  });

  // -----------------------------------------------------------------------
  // Text fixups
  // -----------------------------------------------------------------------
  describe('text fixups', () => {
    describe('removeContinuousDuplicateMarkdownLinks', () => {
      it('deduplicates consecutive identical links', () => {
        const input = '[Google](https://g.com) [Google](https://g.com) [Google](https://g.com)';
        const result = removeContinuousDuplicateMarkdownLinks(input);
        expect(result).to.equal('[Google](https://g.com) ');
      });

      it('does not affect non-duplicate links', () => {
        const input = '[A](https://a.com) [B](https://b.com)';
        expect(removeContinuousDuplicateMarkdownLinks(input)).to.equal(input);
      });
    });

    describe('addSpaceBetweenMarkdownLinks', () => {
      it('adds space between adjacent links', () => {
        const input = '[A](https://a.com)[B](https://b.com)';
        expect(addSpaceBetweenMarkdownLinks(input)).to.equal('[A](https://a.com) [B](https://b.com)');
      });

      it('does not add space when links already have space', () => {
        const input = '[A](https://a.com) [B](https://b.com)';
        expect(addSpaceBetweenMarkdownLinks(input)).to.equal(input);
      });
    });
  });

  // -----------------------------------------------------------------------
  // Message filtering
  // -----------------------------------------------------------------------
  describe('message filtering', () => {
    describe('isIgnoredSlackMessage', () => {
      it('ignores bot_message subtype', () => {
        expect(isIgnoredSlackMessage({ subtype: 'bot_message', ts: '1' } as SlackMessagePayload, {})).to.equal(true);
      });

      it('ignores messages with bot_id', () => {
        expect(isIgnoredSlackMessage({ bot_id: 'B1', ts: '1' } as SlackMessagePayload, {})).to.equal(true);
      });

      it('ignores messages from the bot user itself', () => {
        expect(
          isIgnoredSlackMessage({ user: 'U_BOT', ts: '1' } as SlackMessagePayload, { botUserId: 'U_BOT' }),
        ).to.equal(true);
      });

      it('does not ignore normal user messages', () => {
        expect(
          isIgnoredSlackMessage({ user: 'U1', ts: '1' } as SlackMessagePayload, { botUserId: 'U_BOT' }),
        ).to.equal(false);
      });
    });
  });
});
