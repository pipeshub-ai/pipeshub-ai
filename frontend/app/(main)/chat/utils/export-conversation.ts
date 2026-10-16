import type { ThreadMessageLike } from '@assistant-ui/react';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ChatApi } from '../api';
import { getThreadMessagePlainText, loadHistoricalMessages, prependOlderRows } from '../runtime';
import type { CitationMaps } from '../components/message-area/response-tabs/citations';
import type { ConversationMessage } from '../types';
import { parseArtifactMarkers, parseDownloadMarkers } from './parse-download-markers';
import { saveTextFile } from './save-text-file';

export interface ExportLabels {
  you: string;
  assistant: string;
  sources: string;
}

/**
 * Every stored message of a chat, oldest first. The API returns the newest page first, so each
 * further page goes in front. A page that adds nothing ends the walk, so a server that kept
 * reporting more pages could not loop it forever.
 */
export async function fetchAllConversationRows(
  conversationId: string,
  agentId?: string | null,
): Promise<ConversationMessage[]> {
  let rows: ConversationMessage[] = [];
  for (let page = 1; ; page += 1) {
    const detail = agentId
      ? await AgentsApi.fetchAgentConversation(agentId, conversationId, { page })
      : await ChatApi.fetchConversation(conversationId, page);
    const next = prependOlderRows(rows, detail.messages);
    if (next === rows) break;
    rows = next;
    if (!detail.pagination.hasNextPage) break;
  }
  return rows;
}

/** What the chat shows for an answer: the text without the markers that become download cards. */
function answerText(message: ThreadMessageLike): string {
  const withoutArtifacts = parseArtifactMarkers(getThreadMessagePlainText(message)).text;
  return parseDownloadMarkers(withoutArtifacts).text.trim();
}

function escapeLinkText(text: string): string {
  return text.replace(/[[\]\\]/g, '\\$&');
}

// A source link comes from stored data, so only a web address is turned into a link.
function safeLinkTarget(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    // A `)` or a space in the address would end the Markdown link early.
    return parsed.href.replace(/[()\s]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0')}`);
  } catch {
    return null;
  }
}

function sourceLines(text: string, maps: CitationMaps | undefined): string[] {
  if (!maps) return [];
  const cited = new Set<number>();
  for (const match of text.matchAll(/\[(\d+)\]/g)) cited.add(Number(match[1]));
  const lines: string[] = [];
  for (const index of [...cited].sort((a, b) => a - b)) {
    const citation = maps.citations[maps.citationsOrder[index]];
    if (!citation) continue;
    const name = escapeLinkText(citation.recordName);
    const target = safeLinkTarget(citation.webUrl);
    lines.push(target ? `- [${index}] [${name}](${target})` : `- [${index}] ${name}`);
  }
  return lines;
}

export function buildConversationMarkdown(
  title: string,
  messages: ThreadMessageLike[],
  labels: ExportLabels,
): string {
  const blocks: string[] = [];
  const heading = title.trim();
  if (heading) blocks.push(`# ${heading}`);

  for (const message of messages) {
    if (message.role === 'user') {
      const text = getThreadMessagePlainText(message).trim();
      if (text) blocks.push(`## ${labels.you}\n\n${text}`);
    } else if (message.role === 'assistant') {
      const text = answerText(message);
      if (!text) continue;
      const maps = message.metadata?.custom?.citationMaps as CitationMaps | undefined;
      const sources = sourceLines(text, maps);
      const block = [`## ${labels.assistant}`, text];
      if (sources.length > 0) block.push(`**${labels.sources}**\n\n${sources.join('\n')}`);
      blocks.push(block.join('\n\n'));
    }
  }
  return `${blocks.join('\n\n')}\n`;
}

/** A name every operating system accepts: no path or reserved characters, no trailing dot. */
export function exportFileName(title: string): string {
  const base = title
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
    .replace(/[. ]+$/, '');
  return `${base || 'chat'}.md`;
}

export async function exportConversation(input: {
  conversationId: string;
  title: string;
  agentId?: string | null;
  labels: ExportLabels;
}): Promise<void> {
  const rows = await fetchAllConversationRows(input.conversationId, input.agentId);
  const markdown = buildConversationMarkdown(input.title, loadHistoricalMessages(rows).messages, input.labels);
  saveTextFile(exportFileName(input.title), markdown, 'text/markdown');
}
