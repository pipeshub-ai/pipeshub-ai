'use client';

import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import NextLink from 'next/link';
import { Box, Button, Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { formatBytes } from '../../activity';
import { useBuiltinResults, type BuiltinResult, type BuiltinRow } from '../../builtin-results';
import type { WorkspaceReference } from '../../types';
import {
  confirmUpload,
  csvRows,
  download,
  endKeptSandbox,
  ENGINE_NONE,
  makeEngineMyDefault,
  openFile,
  OPEN_CSV_ROWS,
  OPEN_MAX_LINES,
  pickEngine,
} from '../../sandbox-commands';
import { useEngineReason } from '@/app/(main)/workspace/ai-accounts/components/coding-engine-select';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { startUndo } from '../../builtin-actions';
import { useChatStore } from '../../store';
import { executorLabel } from './builtin-result-rows';

/** Files `/files` shows in one folder; the rest collapse to "+N more" (SLASH3 §3.1). */
export const FOLDER_MAX_SHOWN = 200;

type FilesResult = Extract<BuiltinResult, { kind: 'files' }>;
type OpenResult = Extract<BuiltinResult, { kind: 'open' }>;
type SandboxResult = Extract<BuiltinResult, { kind: 'sandbox' }>;

const MONO: React.CSSProperties = { fontFamily: 'var(--code-font-family, monospace)' };
const TOUCH: React.CSSProperties = { minWidth: 44, minHeight: 44 };

function chatTitle(sessionId: string): string | undefined {
  const s = useChatStore.getState();
  return [...s.conversations, ...s.sharedConversations, ...s.agentConversations].find((c) => c.id === sessionId)?.title;
}

/** "As of the last save, 40 s ago": a reply in progress may hold newer files than the store (O7). */
function LastSaveNote({ running, savedAt }: { running: boolean; savedAt: number }) {
  const { t } = useTranslation();
  // When the row was shown: the note says how old the list was then, and does not tick.
  const [shownAt] = useState(() => Date.now());
  if (!running) return null;
  const seconds = Math.max(0, Math.round((shownAt - savedAt) / 1000));
  return <Text size="1" color="gray">{t('slashCommands.result.files.lastSave', { seconds })}</Text>;
}

interface TreeLine {
  key: string;
  depth: number;
  label: string;
  /** A file's full path; absent for a folder or a "+N more" line. */
  path?: string;
  size?: number;
  hidden?: boolean;
  folder?: boolean;
}

/** The listing as tree lines: each folder once with its total, its files under it, past 200 collapsed. */
export function treeLines(files: FilesResult['list']['files']): TreeLine[] {
  const totals = new Map<string, number>();
  for (const file of files) {
    const parts = file.path.split('/');
    for (let i = 1; i < parts.length; i += 1) {
      const dir = parts.slice(0, i).join('/');
      totals.set(dir, (totals.get(dir) ?? 0) + file.sizeBytes);
    }
  }
  const lines: TreeLine[] = [];
  const opened = new Set<string>();
  const shown = new Map<string, number>();
  for (const file of files) {
    const parts = file.path.split('/');
    for (let i = 1; i < parts.length; i += 1) {
      const dir = parts.slice(0, i).join('/');
      if (opened.has(dir)) continue;
      opened.add(dir);
      lines.push({ key: `d:${dir}`, depth: i - 1, label: `${parts[i - 1]}/`, size: totals.get(dir), folder: true });
    }
    const parent = parts.slice(0, -1).join('/');
    const count = (shown.get(parent) ?? 0) + 1;
    shown.set(parent, count);
    if (count <= FOLDER_MAX_SHOWN) {
      lines.push({ key: `f:${file.path}`, depth: parts.length - 1, label: parts[parts.length - 1], path: file.path, size: file.sizeBytes, hidden: file.hidden });
    } else if (count === FOLDER_MAX_SHOWN + 1) {
      const over = files.filter((f) => f.path.split('/').slice(0, -1).join('/') === parent).length - FOLDER_MAX_SHOWN;
      lines.push({ key: `m:${parent}`, depth: parts.length - 1, label: `+${String(over)}` });
    }
  }
  return lines;
}

/** A plain line per fetched copy (ADR-RUN-01 D2): what it is and that it is not saved; it is never a file to open. */
export function ReferenceLines({ references }: { references: WorkspaceReference[] }) {
  const { t } = useTranslation();
  if (references.length === 0) return null;
  return (
    <Flex direction="column" gap="1" data-testid="sandbox-files-references">
      {references.map((ref) => (
        <Text key={ref.path} size="1" color="gray" title={`${ref.path}/`} data-testid={`sandbox-reference-${ref.path}`}>
          {ref.version
            ? t('slashCommands.result.files.reference', { label: ref.label, version: ref.version.slice(0, 7) })
            : t('slashCommands.result.files.referenceUnversioned', { label: ref.label })}
        </Text>
      ))}
    </Flex>
  );
}

/** `/files`: a keyboard tree (↑↓ move, Enter opens) with ▸ open and ⤓ download on each file (SLASH3 §4). */
export function FilesBody({ result, row, rowKey }: { result: FilesResult; row: BuiltinRow; rowKey: string }) {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const { list, session } = result;
  const lines = useMemo(() => treeLines(list.files), [list.files]);
  const [active, setActive] = useState(0);
  const refs = useRef<Array<HTMLElement | null>>([]);
  const size = (bytes: number | undefined) => (bytes === undefined ? '' : formatBytes(bytes, i18n.language));
  const at = { rowKey, afterPairs: row.afterPairs };
  const open = (path: string) => void openFile(session, path, { ...at, typed: `/open ${path}` }, t, result.running);
  const save = (path: string) =>
    void download(session, path, { folder: false, title: chatTitle(session.sessionId) ?? '' }, { ...at, typed: `/download ${path}` }, t);
  const move = (to: number) => {
    const next = Math.max(0, Math.min(lines.length - 1, to));
    setActive(next);
    refs.current[next]?.focus();
  };
  const references = list.references ?? [];
  if (list.revision === 0 || (list.files.length === 0 && references.length === 0)) {
    return <Text size="1">{t('slashCommands.reason.noWorkspace')}</Text>;
  }
  return (
    <Flex direction="column" gap="2">
      <Text size="1">
        {t('slashCommands.result.files.summary', {
          revision: list.revision,
          count: list.files.length + list.omitted,
          size: size(list.totalBytes),
        })}
      </Text>
      <LastSaveNote running={result.running} savedAt={list.createdAt} />
      <Box
        role="tree"
        aria-label={t('slashCommands.result.files.treeLabel')}
        data-testid="sandbox-files-tree"
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            move(active + 1);
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            move(active - 1);
          } else if (e.key === 'Home') {
            e.preventDefault();
            move(0);
          } else if (e.key === 'End') {
            e.preventDefault();
            move(lines.length - 1);
          } else if (e.key === 'Enter' && e.target === refs.current[active] && lines[active]?.path) {
            e.preventDefault();
            open(lines[active].path as string);
          }
        }}
        style={{ ...MONO, fontSize: 'var(--font-size-1)' }}
      >
        {lines.map((line, i) => (
          <Flex
            key={line.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="treeitem"
            aria-level={line.depth + 1}
            aria-expanded={line.folder ? true : undefined}
            aria-selected={i === active}
            tabIndex={i === active ? 0 : -1}
            onFocus={() => setActive(i)}
            align="center"
            gap="2"
            data-testid={line.path ? `sandbox-file-${line.path}` : undefined}
            style={{
              paddingLeft: `calc(${String(line.depth)} * var(--space-4))`,
              borderBottom: '1px dashed var(--gray-a4)',
              minHeight: isMobile ? 44 : 28,
            }}
          >
            <Text size="1" weight={line.folder ? 'bold' : 'regular'} style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
              {line.folder ? '▾ ' : ''}
              {line.path === undefined && !line.folder ? t('slashCommands.result.files.more', { label: line.label }) : line.label}
              {line.hidden ? (
                <Text size="1" color="gray"> · {t('slashCommands.result.files.hidden')}</Text>
              ) : null}
            </Text>
            <Text size="1" color="gray" style={{ whiteSpace: 'nowrap' }}>{size(line.size)}</Text>
            {line.path ? (
              <Flex gap="1">
                <IconButton
                  size="1"
                  variant="ghost"
                  tabIndex={-1}
                  aria-label={t('slashCommands.result.files.open', { path: line.path })}
                  onClick={() => open(line.path as string)}
                  style={isMobile ? TOUCH : undefined}
                >
                  <MaterialIcon name="chevron_right" size={16} />
                </IconButton>
                <IconButton
                  size="1"
                  variant="ghost"
                  tabIndex={-1}
                  aria-label={t('slashCommands.result.files.download', { path: line.path })}
                  onClick={() => save(line.path as string)}
                  style={isMobile ? TOUCH : undefined}
                >
                  <MaterialIcon name="download" size={16} />
                </IconButton>
              </Flex>
            ) : null}
          </Flex>
        ))}
      </Box>
      {list.omitted > 0 ? <Text size="1" color="gray">{t('slashCommands.result.files.omitted', { count: list.omitted })}</Text> : null}
      <ReferenceLines references={references} />
      <Box>
        <Button
          size="1"
          variant="soft"
          style={{ minHeight: isMobile ? 44 : 28 }}
          onClick={() =>
            void download(session, '', { folder: true, title: chatTitle(session.sessionId) ?? '' }, { ...at, typed: '/download' }, t)
          }
        >
          {t('slashCommands.result.files.downloadAll')}
        </Button>
      </Box>
    </Flex>
  );
}

/** The bytes as a preview: an image from a blob URL, CSV as a table, text with line numbers, else "Binary file". */
export function OpenBody(props: { result: OpenResult; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1">
      {props.result.running ? <Text size="1" color="gray">{t('slashCommands.result.open.lastSave')}</Text> : null}
      <Preview {...props} />
    </Flex>
  );
}

function Preview({ result, row, rowKey }: { result: OpenResult; row: BuiltinRow; rowKey: string }) {
  const { t, i18n } = useTranslation();
  const { read, path, session } = result;
  const [text, setText] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const image = read.contentType.startsWith('image/') && !read.truncated;
  const isText = read.contentType.startsWith('text/plain');
  useEffect(() => {
    if (!image) return undefined;
    const href = URL.createObjectURL(read.blob);
    setUrl(href);
    return () => URL.revokeObjectURL(href);
  }, [image, read.blob]);
  useEffect(() => {
    if (!isText) return;
    let live = true;
    // FileReader, not Blob.text(): every browser and the test DOM have it.
    const reader = new FileReader();
    reader.onload = () => {
      if (live) setText(typeof reader.result === 'string' ? reader.result : '');
    };
    reader.readAsText(read.blob);
    return () => {
      live = false;
    };
  }, [isText, read.blob]);
  const size = formatBytes(read.sizeBytes, i18n.language);
  const save = () =>
    void download(session, path, { folder: false, title: '' }, { rowKey, afterPairs: row.afterPairs, typed: `/download ${path}` }, t);
  const bar = (detail: string) => (
    <Flex justify="between" gap="2" wrap="wrap" style={{ padding: 'var(--space-1) var(--space-2)', borderBottom: '1px solid var(--gray-a5)' }}>
      <Text size="1" color="gray" style={{ ...MONO, overflowWrap: 'anywhere' }}>{detail}</Text>
      <Button size="1" variant="ghost" onClick={save}>{t('slashCommands.result.open.download')}</Button>
    </Flex>
  );
  const frame: React.CSSProperties = { border: '1px solid var(--gray-a5)', borderRadius: 'var(--radius-2)', overflow: 'hidden' };
  if (image) {
    return (
      <Box style={frame}>
        {bar(t('slashCommands.result.open.image', { path, size, revision: read.revision }))}
        {url ? <img src={url} alt={path} style={{ display: 'block', maxWidth: '100%', maxHeight: 1200 }} /> : null}
      </Box>
    );
  }
  if (!isText) {
    return (
      <Box style={frame}>{bar(t('slashCommands.result.open.binary', { path, size }))}</Box>
    );
  }
  if (text === null) return <Text size="1" color="gray">{t('slashCommands.result.loading')}</Text>;
  if (/\.csv$/i.test(path)) {
    const { rows, more } = csvRows(text, OPEN_CSV_ROWS);
    return (
      <Box style={frame}>
        {bar(t('slashCommands.result.open.csv', { path, rows: rows.length, revision: read.revision }))}
        <Box style={{ overflowX: 'auto', maxWidth: '100%' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 'var(--font-size-1)', ...MONO }}>
            <tbody>
              {rows.map((cells, r) => (
                <tr key={r}>
                  {cells.map((cell, c) =>
                    r === 0 ? (
                      <th key={c} scope="col" style={{ textAlign: 'left', padding: '2px 8px', borderBottom: '1px solid var(--gray-a5)' }}>{cell}</th>
                    ) : (
                      <td key={c} style={{ padding: '2px 8px' }}>{cell}</td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </Box>
        {more || read.truncated ? <Text as="p" size="1" color="gray" style={{ padding: 'var(--space-1) var(--space-2)' }}>{t('slashCommands.result.open.moreRows')}</Text> : null}
      </Box>
    );
  }
  const all = text.split('\n');
  const shown = all.slice(0, OPEN_MAX_LINES);
  const rest = all.length - shown.length;
  return (
    <Box style={frame}>
      {bar(t('slashCommands.result.open.text', { path, lines: all.length, revision: read.revision }))}
      {/* Rendered as text nodes only: workspace content is never markup. */}
      <pre data-testid="sandbox-open-text" style={{ margin: 0, padding: 'var(--space-2)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 'var(--font-size-1)', ...MONO }}>
        {shown.map((line, i) => (
          <React.Fragment key={i}>
            <span aria-hidden style={{ color: 'var(--gray-11)', userSelect: 'none', display: 'inline-block', width: '3em' }}>{i + 1}</span>
            {line}
            {'\n'}
          </React.Fragment>
        ))}
      </pre>
      {rest > 0 || read.truncated ? (
        <Text as="p" size="1" color="gray" style={{ padding: 'var(--space-1) var(--space-2)' }}>
          {t('slashCommands.result.open.moreLines', { count: Math.max(rest, 1) })}
        </Text>
      ) : null}
    </Box>
  );
}

/** The broker's isolation as people say it; nothing when unknown. */
function isolationLabel(t: ReturnType<typeof useTranslation>['t'], isolation: string | null): string | null {
  if (isolation === 'container_gvisor') return t('slashCommands.result.sandbox.gvisor');
  if (isolation === 'container') return t('slashCommands.result.sandbox.runc');
  return null;
}

/** `/sandbox` (SLASH3 §3.6): one card; the only action is "End now" for a kept sign-in sandbox (SD8). */
export function SandboxBody({ result, row, rowKey }: { result: SandboxResult; row: BuiltinRow; rowKey: string }) {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const { view } = result;
  const image = [view.image.name, isolationLabel(t, view.image.isolation)].filter(Boolean).join(' · ');
  const rows: Array<[string, React.ReactNode]> = [
    [
      t('slashCommands.result.sandbox.executor'),
      <span key="executor">
        {executorLabel(t, result.executor) ?? t('slashCommands.result.sandbox.noRunYet')}
        {view.codingEngine ? (
          <Text color="gray">
            {' · '}
            {t('slashCommands.result.sandbox.nextMessage', {
              engine: view.codingEngine === ENGINE_NONE ? t('slashCommands.result.engine.none') : engineName(view.codingEngine),
            })}
          </Text>
        ) : null}
      </span>,
    ],
    [t('slashCommands.result.sandbox.codeRunsIn'), <span key="image" style={MONO}>{image}</span>],
    [t('slashCommands.result.sandbox.network'), view.network ? t('slashCommands.result.sandbox.networkProxy') : t('slashCommands.result.sandbox.networkNone')],
    [
      t('slashCommands.result.sandbox.approvals'),
      view.codeRuns === 'deny'
        ? t('slashCommands.builtin.run.reason.denied')
        : view.codeRuns === 'ask'
          ? t('slashCommands.result.sandbox.approvalsAsk')
          : t('slashCommands.result.sandbox.approvalsAllow'),
    ],
    [
      t('slashCommands.result.sandbox.workspace'),
      view.workspace.revision === 0
        ? t('slashCommands.reason.noWorkspace')
        : t('slashCommands.result.sandbox.workspaceValue', {
            revision: view.workspace.revision,
            count: view.workspace.files,
            size: formatBytes(view.workspace.bytes, i18n.language),
          }),
    ],
  ];
  const kept = result.kept === 'ended' ? null : view.keptSignIn;
  if (kept) {
    rows.push([t('slashCommands.result.sandbox.kept'), t('slashCommands.result.sandbox.keptValue', { idle: kept.idleMinutes, hours: kept.maxHours })]);
  }
  const height = isMobile ? 44 : 28;
  const asking = result.kept === 'confirm' || result.kept === 'busy';
  return (
    <Flex direction="column" gap="2" data-testid="sandbox-card">
      <Box style={{ display: 'grid', gridTemplateColumns: 'max-content minmax(0, 1fr)', columnGap: 'var(--space-3)', rowGap: 'var(--space-1)' }}>
        {rows.map(([key, value]) => (
          <React.Fragment key={key}>
            <Text size="1" color="gray">{key}</Text>
            <Text size="1" style={{ overflowWrap: 'anywhere' }}>{value}</Text>
          </React.Fragment>
        ))}
      </Box>
      {kept && asking ? (
        <Flex direction="column" gap="1" role="group" aria-label={t('slashCommands.result.sandbox.endConfirm')}>
          <Text size="1">{t('slashCommands.result.sandbox.endConfirm')}</Text>
          <Flex gap="2" wrap="wrap">
            <Button size="1" color="red" disabled={result.kept === 'busy'} style={{ minHeight: height }} onClick={() => void endKeptSandbox(rowKey, row.id, true)}>
              {t('slashCommands.result.sandbox.endNow')}
            </Button>
            <Button
              size="1"
              variant="soft"
              color="gray"
              disabled={result.kept === 'busy'}
              style={{ minHeight: height }}
              onClick={() => useBuiltinResults.getState().update(rowKey, row.id, { ...result, kept: undefined })}
            >
              {t('durableRun.versions.cancel')}
            </Button>
          </Flex>
        </Flex>
      ) : null}
      {kept && !asking ? (
        <Box>
          <Button size="1" variant="soft" style={{ minHeight: height }} onClick={() => void endKeptSandbox(rowKey, row.id, false)}>
            {t('slashCommands.result.sandbox.endNowAsk')}
          </Button>
        </Box>
      ) : null}
      <Text size="1" color={result.kept === 'failed' ? 'red' : 'gray'} aria-live="polite">
        {result.kept === 'ended'
          ? t('slashCommands.result.sandbox.ended')
          : result.kept === 'failed'
            ? t('slashCommands.result.failed')
            : kept
              ? t('slashCommands.result.sandbox.footKept')
              : t('slashCommands.result.sandbox.foot')}
      </Text>
    </Flex>
  );
}

type UploadConfirmResult = Extract<BuiltinResult, { kind: 'uploadConfirm' }>;
type UploadedResult = Extract<BuiltinResult, { kind: 'uploaded' }>;

/** "Replace 2 files?" before an upload that would overwrite workspace files (SD6). */
export function UploadConfirmBody({ result, row, rowKey }: { result: UploadConfirmResult; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const height = isMobile ? 44 : 28;
  return (
    <Flex direction="column" gap="2" role="group" aria-label={t('slashCommands.result.upload.replace', { count: result.replaced.length })}>
      <Text size="1">{t('slashCommands.result.upload.replace', { count: result.replaced.length })}</Text>
      <Box style={{ ...MONO, fontSize: 'var(--font-size-1)' }}>
        {result.replaced.slice(0, 10).map((path) => (
          <Text as="div" size="1" key={path} style={{ overflowWrap: 'anywhere' }}>{path}</Text>
        ))}
      </Box>
      <Flex gap="2" wrap="wrap">
        <Button size="1" color="red" disabled={result.busy === true} style={{ minHeight: height }} onClick={() => void confirmUpload(rowKey, row.id)}>
          {t('slashCommands.result.upload.replaceButton')}
        </Button>
        <Button
          size="1"
          variant="soft"
          color="gray"
          disabled={result.busy === true}
          style={{ minHeight: height }}
          onClick={() => useBuiltinResults.getState().dismiss(rowKey, row.id)}
        >
          {t('durableRun.versions.cancel')}
        </Button>
      </Flex>
    </Flex>
  );
}

/** What an upload added, as one version; Undo is the `/undo` flow (it is the workspace's last turn). */
export function UploadedBody({ result, row, rowKey }: { result: UploadedResult; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const session = result.session;
  return (
    <Flex direction="column" gap="2" data-testid="sandbox-uploaded">
      <Text size="1">
        {result.paths
          ? t('slashCommands.result.upload.added', { count: result.paths.length, revision: result.revision })
          : t('slashCommands.result.upload.addedSaved', { revision: result.revision })}
      </Text>
      {result.paths ? (
        <Box style={{ ...MONO, fontSize: 'var(--font-size-1)' }}>
          {result.paths.slice(0, 10).map((path) => (
            <Text as="div" size="1" key={path} style={{ overflowWrap: 'anywhere' }}>{path}</Text>
          ))}
          {result.paths.length > 10 ? (
            <Text size="1" color="gray">{t('slashCommands.result.files.omitted', { count: result.paths.length - 10 })}</Text>
          ) : null}
        </Box>
      ) : null}
      <Text size="1" color="gray">{t('slashCommands.result.upload.notAttachment')}</Text>
      {session ? (
        <Box>
          <Button
            size="1"
            variant="soft"
            color="gray"
            style={{ minHeight: isMobile ? 44 : 28 }}
            onClick={() => void startUndo(session, { rowKey, afterPairs: row.afterPairs, typed: '/undo' }, t)}
          >
            {t('slashCommands.result.run.undo')}
          </Button>
        </Box>
      ) : null}
    </Flex>
  );
}

type EngineResult = Extract<BuiltinResult, { kind: 'engine' }>;

/**
 * `/engine` (SLASH3 §3.7): this chat's coding engine from its next message, as radio choices; an engine the org
 * cannot use now is disabled with its reason. "As my default" names what it runs now; "Make this my default"
 * saves the chat's engine as the person's own (CMDUI), and My AI accounts is where to change it otherwise.
 */
export function EngineBody({ result, row, rowKey }: { result: EngineResult; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const reasonOf = useEngineReason();
  const name = useId();
  const nameOf = (kind: string) => (kind === ENGINE_NONE ? t('slashCommands.result.engine.noEngine') : engineName(kind));
  const choices: Array<{ value: string | null; label: string; available: boolean; reason: string | null }> = [
    {
      value: null,
      label: t('slashCommands.result.engine.asDefaultIs', { engine: nameOf(result.myDefault) }),
      available: true,
      reason: null,
    },
    ...(result.none ? [{ value: ENGINE_NONE, label: t('slashCommands.result.engine.none'), available: true, reason: null }] : []),
    ...result.options.map((option) => ({ value: option.kind, label: engineName(option.kind), available: option.available, reason: option.reason })),
  ];
  return (
    <Flex direction="column" gap="2" data-testid="sandbox-engine">
      <Text size="1">
        {t('slashCommands.result.engine.heading')}{' '}
        <Text color="gray">· {t(result.session ? 'slashCommands.result.engine.nextMessage' : 'slashCommands.result.engine.firstMessageHeading')}</Text>
      </Text>
      <Flex direction="column" gap="1" role="radiogroup" aria-label={t('slashCommands.result.engine.heading')}>
        {choices.map((choice) => (
          <label
            key={choice.value ?? 'default'}
            style={{
              display: 'grid',
              gridTemplateColumns: 'auto 1fr',
              columnGap: 'var(--space-2)',
              alignItems: 'baseline',
              padding: 'var(--space-1) var(--space-2)',
              border: '1px solid var(--gray-a5)',
              borderRadius: 'var(--radius-2)',
              minHeight: isMobile ? 44 : 32,
              color: choice.available ? undefined : 'var(--gray-10)',
            }}
          >
            <input
              type="radio"
              name={name}
              checked={result.current === choice.value}
              disabled={!choice.available || result.busy === true}
              onChange={() => void pickEngine(rowKey, row.id, choice.value)}
            />
            <Text size="1">{choice.label}</Text>
            {!choice.available ? (
              <Text size="1" color="gray" style={{ gridColumn: 2 }}>{reasonOf(choice.reason)}</Text>
            ) : null}
          </label>
        ))}
      </Flex>
      <Text size="1" color={result.unknown ? undefined : 'gray'} aria-live="polite">
        {result.unknown
          ? t('slashCommands.result.engine.unknown', { name: result.unknown })
          : result.refused
            ? t('slashCommands.result.engine.refused')
            : result.saved
              ? t(result.session ? 'slashCommands.result.engine.saved' : 'slashCommands.result.engine.firstMessage')
              : result.running
                ? t('slashCommands.result.runKeepsPins')
                : t(result.session ? 'slashCommands.result.engine.carryOver' : 'slashCommands.result.engine.firstMessageHint')}
      </Text>
      <Flex align="center" gap="3" wrap="wrap">
        {result.current !== null && result.current !== result.myDefault ? (
          <Button
            size={isMobile ? '2' : '1'}
            variant="soft"
            disabled={result.makeDefault === 'busy'}
            onClick={() => void makeEngineMyDefault(rowKey, row.id)}
          >
            {t('slashCommands.result.engine.makeDefault')}
          </Button>
        ) : null}
        <NextLink href="/workspace/ai-accounts" style={{ color: 'var(--accent-11)', fontSize: 'var(--font-size-1)' }}>
          {t('aiAccounts.my.title')}
        </NextLink>
      </Flex>
      {result.makeDefault === 'saved' || result.makeDefault === 'failed' ? (
        <Text size="1" color={result.makeDefault === 'failed' ? 'red' : 'gray'} aria-live="polite">
          {t(result.makeDefault === 'saved' ? 'slashCommands.result.engine.defaultSaved' : 'slashCommands.result.engine.defaultFailed')}
        </Text>
      ) : null}
    </Flex>
  );
}
