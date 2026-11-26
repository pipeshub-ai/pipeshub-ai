'use client';

/**
 * How a tool call's arguments and result read (the chat timeline's step and the Activity panel's, ACTRESULTS):
 * one renderer, so both show the same thing the same way.
 */

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Table, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCopyText } from '@/lib/hooks/use-copy-text';
import type { MessagePart } from '../../types';
import { looksLikeJson, safeHref, toolResultView, type ToolResultView } from '../../tool-result-view';
import { redactedArgs, redactText } from '../../approval-preview';
import { extractSqlQuery, isMcpTool, isSkillTool, isWebTool } from '../../utils/tool-display';
import { CodeBlock } from './answer-content';

export type ToolStatus = 'running' | 'completed' | 'failed' | 'blocked' | 'awaiting_approval';

export const toolStatus = (status: MessagePart['status'], fallback: ToolStatus): ToolStatus =>
  status === 'running' || status === 'completed' || status === 'failed' || status === 'blocked' || status === 'awaiting_approval'
    ? status
    : fallback;

const LINK_STYLE: React.CSSProperties = { color: 'var(--accent-11)' };
// A link ends at whitespace, a quote or a closing bracket; trailing punctuation is the sentence's, not the URL's.
const URL_IN_TEXT = /\b(?:https?:\/\/|mailto:)[^\s"'<>\])}]+/g;
const TRAILING = /[.,;:!?]+$/;

/** Text with its http(s) and mailto links clickable, in a new tab; anything else (a `javascript:` URL) stays text. */
export function LinkedText({ text }: { text: string }) {
  const pieces = useMemo(() => {
    const out: React.ReactNode[] = [];
    let at = 0;
    for (const match of text.matchAll(URL_IN_TEXT)) {
      const found = match[0].replace(TRAILING, '');
      const start = match.index ?? 0;
      const href = safeHref(found);
      if (!href) continue;
      if (start > at) out.push(text.slice(at, start));
      out.push(
        <a key={start} href={href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>
          {found}
        </a>,
      );
      at = start + found.length;
    }
    if (at < text.length) out.push(text.slice(at));
    return out;
  }, [text]);
  return <>{pieces}</>;
}

/** Renders a server-computed tool summary (e.g. "Retrieved 12 blocks from
 * 5 documents\n- Doc A\n- Doc B") as styled text rather than monospace —
 * summaries are prose/bullet-list, not raw JSON, so they read better with
 * the same lightweight markdown treatment `NarrationText` uses. A summary can
 * quote a tool's output (a title), so it loads no images and links only to
 * http(s) and mailto, in a new tab. */
export function ToolSummaryText({ content }: { content: string }) {
  return (
    <Box style={{ fontSize: 'var(--font-size-1)', color: 'var(--slate-11)', lineHeight: 1.6 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        disallowedElements={['img']}
        urlTransform={(url) => safeHref(url) ?? ''}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>
                {children}
              </a>
            ) : (
              <>{children}</>
            ),
          p: ({ children }) => (
            <Text size="1" as="p" style={{ margin: '0 0 var(--space-1) 0', color: 'inherit' }}>
              {children}
            </Text>
          ),
          ul: ({ children }) => (
            <ul style={{ paddingLeft: 'var(--space-4)', margin: 0, listStyleType: 'disc' }}>{children}</ul>
          ),
          li: ({ children }) => (
            <li style={{ marginBottom: '2px', lineHeight: 1.5 }}>{children}</li>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
}

/** Pretty-prints `raw` when it is JSON. A truncated preview is no longer
 * valid JSON, so it is shown as-is. */
export function formatToolPayload(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function isEmptyToolArgs(raw: string | undefined): boolean {
  const trimmed = raw?.trim();
  return !trimmed || trimmed === '{}' || trimmed === 'null';
}

/** Monospace, scrollable view of a raw tool payload with a copy button. */
function RawToolPayload({ raw }: { raw: string }) {
  const { t } = useTranslation();
  const { copied, copy } = useCopyText();
  const text = useMemo(() => formatToolPayload(raw), [raw]);

  return (
    <Box
      style={{
        position: 'relative',
        marginTop: 'var(--space-1)',
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-1)',
      }}
    >
      <button
        type="button"
        onClick={() => void copy(text)}
        aria-label={copied ? t('chatStream.copiedCode') : t('chatStream.copyCode')}
        style={{
          position: 'absolute',
          top: 4,
          right: 4,
          display: 'inline-flex',
          alignItems: 'center',
          background: 'var(--slate-3)',
          border: 'none',
          borderRadius: 'var(--radius-1)',
          padding: 2,
          cursor: 'pointer',
        }}
      >
        <MaterialIcon
          name={copied ? 'check' : 'content_copy'}
          size={13}
          color={copied ? 'var(--accent-11)' : 'var(--slate-11)'}
        />
      </button>
      <Box
        onWheel={(e) => e.stopPropagation()}
        style={{
          maxHeight: 240,
          overflow: 'auto',
          padding: 'var(--space-2)',
          paddingRight: 'var(--space-6)',
          fontFamily: 'monospace',
          fontSize: 'var(--font-size-1)',
          color: 'var(--slate-11)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        <LinkedText text={text} />
      </Box>
    </Box>
  );
}

/** A tool's table of records or one record's fields, from its full result. */
function ToolResultViewBlock({ view }: { view: ToolResultView }) {
  const { t } = useTranslation();
  if (view.kind === 'fields') {
    return (
      <Box
        data-testid="tool-result-fields"
        style={{
          marginTop: 'var(--space-1)',
          padding: 'var(--space-2)',
          border: '1px solid var(--slate-5)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-1)',
        }}
      >
        <Box style={{ display: 'grid', gridTemplateColumns: 'minmax(5rem, max-content) 1fr', columnGap: 'var(--space-3)', rowGap: 4 }}>
          {view.fields.map((field, index) => (
            <React.Fragment key={index}>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {field.label}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
                {field.value}
              </Text>
            </React.Fragment>
          ))}
        </Box>
        {view.url && (
          <Text as="p" size="1" mt="2">
            <a href={view.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-11)' }}>
              {t('chat.toolCard.open')}
            </a>
          </Text>
        )}
      </Box>
    );
  }
  return (
    <Box
      data-testid="tool-result-table"
      onWheel={(e) => e.stopPropagation()}
      style={{ marginTop: 'var(--space-1)', maxHeight: 320, overflow: 'auto' }}
    >
      <Table.Root size="1" variant="surface">
        <Table.Header>
          <Table.Row>
            {view.columns.map((column, index) => (
              <Table.ColumnHeaderCell key={index} style={{ whiteSpace: 'nowrap' }}>
                {column}
              </Table.ColumnHeaderCell>
            ))}
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {view.rows.map((row, rowIndex) => (
            <Table.Row key={rowIndex}>
              {row.cells.map((cell, index) => (
                <Table.Cell key={index} style={{ maxWidth: 280, overflowWrap: 'anywhere' }}>
                  {index === 0 && row.url ? (
                    <a href={row.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-11)' }}>
                      {cell || row.url}
                    </a>
                  ) : (
                    cell
                  )}
                </Table.Cell>
              ))}
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
      {view.total > view.rows.length && (
        <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-11)' }}>
          {t('chat.toolCard.showingOf', { shown: view.rows.length, total: view.total })}
        </Text>
      )}
    </Box>
  );
}

/** Something collapsed behind a toggle: closed, it is one line ("Show result", "Details"). */
function Collapsed({ show, hide, testId, children }: { show: string; hide: string; testId?: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Box data-testid={testId}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        style={{
          appearance: 'none',
          background: 'none',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          color: 'var(--slate-11)',
          fontSize: 'var(--font-size-1)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 2,
        }}
      >
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={14} color="var(--slate-11)" />
        {open ? hide : show}
      </button>
      {open && children}
    </Box>
  );
}

/** The raw result behind a toggle, when a view of it is shown. */
function RawResultToggle({ raw }: { raw: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Box>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        style={{
          appearance: 'none',
          background: 'none',
          border: 'none',
          padding: 0,
          marginTop: 'var(--space-1)',
          cursor: 'pointer',
          color: 'var(--slate-11)',
          fontSize: 'var(--font-size-1)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 2,
        }}
      >
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={14} color="var(--slate-11)" />
        {open ? t('chat.toolCard.hideRaw') : t('chat.toolCard.viewRaw')}
      </button>
      {open && <RawToolPayload raw={raw} />}
    </Box>
  );
}

/** A result that is prose, shown as text rather than code. */
function PlainResultText({ text }: { text: string }) {
  return (
    <Box
      data-testid="tool-result-text"
      onWheel={(e) => e.stopPropagation()}
      style={{
        marginTop: 'var(--space-1)',
        maxHeight: 240,
        overflow: 'auto',
        fontSize: 'var(--font-size-1)',
        color: 'var(--slate-11)',
        lineHeight: 1.5,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      <LinkedText text={text} />
    </Box>
  );
}

const RESULT_LABEL_KEY: Record<ToolStatus, string> = {
  running: 'chat.toolCard.result',
  completed: 'chat.toolCard.result',
  failed: 'chat.toolCard.error',
  blocked: 'chat.toolCard.blocked',
  awaiting_approval: 'chat.toolCard.awaitingApproval',
};

function ResultLabel({ status }: { status: ToolStatus }) {
  const { t } = useTranslation();
  return (
    <Text size="1" weight="medium" style={{ color: status === 'failed' ? 'var(--red-11)' : 'var(--slate-11)' }}>
      {t(RESULT_LABEL_KEY[status])}
    </Text>
  );
}

const TOOL_TEXT_STYLE: React.CSSProperties = {
  fontFamily: 'monospace',
  fontSize: 'var(--font-size-1)',
  color: 'var(--slate-11)',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

/** A code step's program and what it printed: its one-line summaries alone hide what ran (CODEAUTO-1). */
function codeRunOf(part: MessagePart): { code: string; output: string } | null {
  if (part.toolName?.replace(/^.*__/, '') !== 'run_code') return null;
  const parse = (raw: string | undefined): Record<string, unknown> | null => {
    try {
      const value: unknown = JSON.parse(raw ?? '');
      return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  };
  const args = parse(part.args);
  const result = parse(part.resultPreview);
  const text = (value: unknown) => (typeof value === 'string' ? value.replace(/\n+$/, '') : '');
  // A preview cut at its bound is no longer JSON; it is shown as it came.
  const output = result ? [text(result.stdout), text(result.stderr)].filter(Boolean).join('\n') : (part.resultPreview ?? '');
  return { code: text(args?.code), output };
}

/** What the call asked for: the server's summary, or the masked raw arguments. An MCP call also shows its raw
 * arguments, since an external server's payload is all the person can check it against. */
export function ToolArguments({ part }: { part: MessagePart }) {
  const { t } = useTranslation();
  const codeRun = useMemo(() => codeRunOf(part), [part]);
  const mcp = isMcpTool(part.toolName);
  const hasRawArgs = !isEmptyToolArgs(part.args);
  const sqlQuery = mcp ? undefined : extractSqlQuery(part.toolName, part.args);
  if (mcp ? !part.argsSummary && !hasRawArgs : !part.argsSummary && !part.args) return null;
  return (
    <Box data-testid="tool-arguments">
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {t('chat.toolCard.arguments')}
      </Text>
      {mcp ? (
        <>
          {part.argsSummary && <ToolSummaryText content={redactText(part.argsSummary).text} />}
          {hasRawArgs && <RawToolPayload raw={redactedArgs(part.args ?? '')} />}
        </>
      ) : (
        <>
          {part.argsSummary ? (
            <ToolSummaryText content={redactText(part.argsSummary).text} />
          ) : (
            <Box style={TOOL_TEXT_STYLE}>{redactedArgs(part.args ?? '')}</Box>
          )}
          {part.argsSummary && codeRun?.code ? <RawToolPayload raw={redactText(codeRun.code).text} /> : null}
        </>
      )}
      {sqlQuery && (
        <Box>
          <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
            {t('chat.toolCard.query')}
          </Text>
          {/* No copy or download for a cut-off query: it can still run, e.g. when the cut falls in a `--` comment. */}
          <CodeBlock
            language="sql"
            codeText={redactText(sqlQuery.text).text}
            actions={!sqlQuery.cutOff}
            maxHeight="260px"
            style={{ marginBottom: 0, marginTop: 'var(--space-1)' }}
          />
          {sqlQuery.cutOff && (
            <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-11)' }}>
              {t('chat.toolCard.queryCutOff')}
            </Text>
          )}
        </Box>
      )}
    </Box>
  );
}

export interface ToolResultProps {
  part: MessagePart;
  status: ToolStatus;
  /**
   * Keep the bounded preview a summary stands in for behind "View raw" (the Activity panel, where a person checks
   * an answer against what the tool returned). The chat shows only the summary.
   */
  rawBehindSummary?: boolean;
}

/** What the call returned, as the chat shows it; null when there is nothing to show. */
export function ToolResult({ part, status, rawBehindSummary = false }: ToolResultProps) {
  return isMcpTool(part.toolName) ? (
    <McpToolResult part={part} status={status} />
  ) : (
    <BuiltinToolResult part={part} status={status} rawBehindSummary={rawBehindSummary} />
  );
}

/** A built-in or toolset tool: the server's summary, or the raw text when there is none. Web tools
 * also list what they found (every search result, the page read), each linked. */
function BuiltinToolResult({ part, status, rawBehindSummary }: ToolResultProps) {
  const view = useMemo(
    () => (status === 'completed' && isWebTool(part.toolName) ? toolResultView(part.resultView) : null),
    [status, part.toolName, part.resultView],
  );
  const codeRun = useMemo(() => codeRunOf(part), [part]);
  // A skill tool's raw `resultPreview` is a truncated SKILL.md body or bundled-file content
  // (see `LoadSkillTool`/`LoadSkillResourceTool.summarize_result`): never worth showing.
  // A failed call is worded by the card's failure line (`toolFailureText`).
  const rawShown = Boolean(part.resultPreview) && !isSkillTool(part.toolName);
  if (status === 'failed' || !(part.resultSummary || rawShown)) return null;
  const preview = redactText(part.resultPreview ?? '').text;
  return (
    <Box data-testid="tool-result">
      <ResultLabel status={status} />
      {part.resultSummary ? (
        <ToolSummaryText content={redactText(part.resultSummary).text} />
      ) : (
        <Box data-testid="tool-result-preview" style={TOOL_TEXT_STYLE}>
          <LinkedText text={preview} />
        </Box>
      )}
      {part.resultSummary && codeRun?.output ? <RawToolPayload raw={redactText(codeRun.output).text} /> : null}
      {view && <ToolResultViewBlock view={view} />}
      {rawBehindSummary && part.resultSummary && rawShown && !codeRun?.output ? <RawResultToggle raw={preview} /> : null}
    </Box>
  );
}

/** An MCP tool: the summary, and a table or fields of what it found with the raw output. */
function McpToolResult({ part, status }: { part: MessagePart; status: ToolStatus }) {
  const showRawResult = Boolean(part.resultPreview);
  const rawResult = part.resultPreview ?? '';
  const view = useMemo(() => (status === 'completed' ? toolResultView(part.resultView) : null), [status, part.resultView]);
  const plainResult = showRawResult && !view && !looksLikeJson(rawResult);
  // Prose whose first line is the summary needn't say it twice.
  const summaryShown = Boolean(part.resultSummary) && !(plainResult && part.resultSummary?.trim() === rawResult.trim().split('\n')[0]?.trim());
  if (!(part.resultSummary || showRawResult || view)) return null;
  return (
    <Box data-testid="tool-result">
      <ResultLabel status={status} />
      {summaryShown && <ToolSummaryText content={part.resultSummary ?? ''} />}
      {view && <ToolResultViewBlock view={view} />}
      {showRawResult &&
        (view ? (
          <RawResultToggle raw={rawResult} />
        ) : plainResult ? (
          <PlainResultText text={rawResult} />
        ) : (
          <RawToolPayload raw={rawResult} />
        ))}
    </Box>
  );
}

/** The chat timeline's expanded step: what the call asked for, then what it returned. A step whose row already
 * names its page, query or file (WP-112) keeps its result behind "Show result" and its raw arguments behind
 * "Details". */
export function ToolCallDetails({ part, status }: { part: MessagePart; status: ToolStatus }) {
  if (part.step) return <StepDetails part={part} status={status} />;
  return (
    <>
      <ToolArguments part={part} />
      <ToolResult part={part} status={status} />
    </>
  );
}

function StepDetails({ part, status }: { part: MessagePart; status: ToolStatus }) {
  const { t } = useTranslation();
  const hasResult =
    status !== 'failed' && Boolean(part.resultSummary || part.resultPreview || toolResultView(part.resultView));
  const hasArgs = !isEmptyToolArgs(part.args);
  if (!part.argsSummary && !hasResult && !hasArgs) return null;
  return (
    <>
      {part.argsSummary && <ToolSummaryText content={redactText(part.argsSummary).text} />}
      {hasResult && (
        <Collapsed show={t('chat.toolCard.showResult')} hide={t('chat.toolCard.hideResult')} testId="step-result">
          <ToolResult part={part} status={status} rawBehindSummary />
        </Collapsed>
      )}
      {hasArgs && (
        <Collapsed show={t('chat.toolCard.details')} hide={t('chat.toolCard.hideDetails')} testId="step-details">
          <RawToolPayload raw={redactedArgs(part.args ?? '')} />
        </Collapsed>
      )}
    </>
  );
}
