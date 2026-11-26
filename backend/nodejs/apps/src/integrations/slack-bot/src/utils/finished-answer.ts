import {
  type CitationData,
  addSpaceBetweenMarkdownLinks,
  buildFinalSlackChunks,
  buildFrontendRecordUrl,
  getFrontendBaseUrl,
  removeContinuousDuplicateMarkdownLinks,
  resolveSlackArtifactLink,
  splitSlackBlocksByLimit,
} from '../helpers';
import { rewriteCitationsForSlack, stripFragmentDirective } from './citations';
import { cutShortBlocks, withOutcomeClosing } from './cut-short';
import { parseArtifactMarkers } from './parse-artifact-markers';
import { safeLinkLabel, slackLink } from './slack-escape';

/** A saved answer as the conversation carries it (the newest `bot_response`). */
export interface FinishedBotResponse {
  content: string;
  citations?: CitationData[];
  messageType?: string;
  truncatedBy?: string;
  budgetScope?: string;
  runOutcome?: { closing?: unknown };
}

/**
 * How a finished answer reads in a Slack thread, in the order it is posted: the answer's blocks (one message per
 * chunk), then the cut-short notice and the sources, then one line per file it made. The bot posts it at the end of
 * its stream and Node posts it into the thread when a run ends after its stream handed off (ADR-OWD-09 D-SLACK-ASK),
 * so a person reads the same answer either way.
 */
/** One Block Kit block, as Slack's Web API takes it. */
export type SlackBlock = Record<string, unknown>;

export interface FinishedAnswer {
  answer: SlackBlock[][];
  followUps: SlackBlock[][];
  artifacts: Array<{ key: string; link: string; text: string }>;
}

function citationWebUrl(webUrl?: string): string {
  if (!webUrl) return '';
  return /^https?:\/\//i.test(webUrl)
    ? webUrl
    : `${getFrontendBaseUrl()}${webUrl}`;
}

/** The answer's sources, one link per record, under a "Sources:" heading. */
export function citationSourceBlocks(citations?: CitationData[]): SlackBlock[] {
  const seen = new Set<string>();
  const records: Array<{ name: string; url: string }> = [];
  for (const citation of citations || []) {
    const recordId = citation.citationData.metadata.recordId;
    if (!recordId || seen.has(recordId)) continue;
    seen.add(recordId);
    const webUrl =
      citationWebUrl(citation.citationData.metadata.webUrl) ||
      buildFrontendRecordUrl(recordId);
    // Slack does not implement text fragments, and sources are deduped per record.
    records.push({
      name: citation.citationData.metadata.recordName || 'Source',
      url: stripFragmentDirective(webUrl),
    });
  }
  const blocks: SlackBlock[] = [];
  let elements: SlackBlock[] = [];
  const flush = (): void => {
    blocks.push({
      type: 'rich_text',
      elements: [{ type: 'rich_text_section', elements }],
    });
    elements = [];
  };
  for (const record of records) {
    elements.push(
      {
        type: 'link',
        url: record.url,
        text: ` ${safeLinkLabel(record.url, record.name)}`,
      },
      { type: 'text', text: '\n' },
    );
    if (elements.length === 20) flush();
  }
  if (elements.length > 0) flush();
  return blocks.length > 0
    ? [
        { type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } },
        ...blocks,
      ]
    : [];
}

/** The newest answer of a finished conversation, with its outcome's closing line; null when it has none. */
export function newestBotResponse(
  messages: unknown,
): FinishedBotResponse | null {
  if (!Array.isArray(messages)) return null;
  const found = [...(messages as Array<FinishedBotResponse | null>)]
    .reverse()
    .find((message) => message?.messageType === 'bot_response');
  return found == null
    ? null
    : withOutcomeClosing({
        ...found,
        content: typeof found.content === 'string' ? found.content : '',
      });
}

export async function finishedAnswer(
  response: FinishedBotResponse,
): Promise<FinishedAnswer> {
  const { text, artifacts } = parseArtifactMarkers(response.content || '');
  let body = rewriteCitationsForSlack(
    text,
    response.citations,
    getFrontendBaseUrl(),
  );
  body = addSpaceBetweenMarkdownLinks(
    removeContinuousDuplicateMarkdownLinks(body),
  );
  const cutShort = cutShortBlocks(response.truncatedBy, response.budgetScope);
  const files: FinishedAnswer['artifacts'] = [];
  const seen = new Set<string>();
  for (const artifact of artifacts) {
    const link = resolveSlackArtifactLink(artifact);
    if (!link) continue;
    const key = artifact.recordId || link;
    if (seen.has(key) || seen.has(link)) continue;
    seen.add(key);
    seen.add(link);
    files.push({
      key,
      link,
      text: `*Artifact:* ${slackLink(link, artifact.fileName)}`,
    });
  }
  return {
    answer: (await buildFinalSlackChunks(body)) as SlackBlock[][],
    followUps: [
      ...(cutShort !== null ? [cutShort] : []),
      ...(splitSlackBlocksByLimit(
        citationSourceBlocks(response.citations),
      ) as SlackBlock[][]),
    ],
    artifacts: files,
  };
}
