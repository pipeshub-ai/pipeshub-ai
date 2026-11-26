'use client';

import React, { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { Box, Button, Flex, Spinner, Text } from '@radix-ui/themes';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { JobBriefHistory } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief-history';
import type { DurableAgentRunHandle } from '../../types';
import { safeHref } from '../../tool-result-view';
import { describeTool, toolActivityLabel } from '../../utils/tool-display';
import { JobsApi, briefHistoryView, briefView, type BriefRow, type BriefTaskView, type Words } from './job-data';

type Read<T> = { status: 'loading' } | { status: 'ready'; value: T } | { status: 'failed' };

/** One read per mount: the panel mounts when it opens, so the brief is read when it opens and never polled (§6). */
function useRead<T>(read: () => Promise<T>, enabled: boolean): { state: Read<T>; retry: () => void } {
  const [state, setState] = useState<Read<T>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    setState({ status: 'loading' });
    read()
      .then((value) => alive && setState({ status: 'ready', value }))
      .catch(() => alive && setState({ status: 'failed' }));
    return () => {
      alive = false;
    };
  }, [read, enabled, attempt]);
  return { state, retry: useCallback(() => setAttempt((n) => n + 1), []) };
}

function Rows({ label, rows }: { label: string; rows: ReadonlyArray<BriefRow> }) {
  if (!rows.length) return null;
  return (
    <Flex direction="column" gap="1">
      <Text size="1" weight="medium" color="gray">
        {label}
      </Text>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
        {rows.map((row, i) => (
          <li key={`${row.icon}-${i}`} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
            <span aria-hidden="true" style={{ display: 'inline-flex', marginTop: 2 }}>
              <MaterialIcon name={row.icon} size={16} color="var(--gray-11)" />
            </span>
            <Flex direction="column">
              <Text size="2">{row.text}</Text>
              {row.note ? (
                <Text size="1" color="gray">
                  {row.note}
                </Text>
              ) : null}
            </Flex>
          </li>
        ))}
      </ul>
    </Flex>
  );
}

/** A fold of the brief: closed by default, its row a native button (44 px on the phone). */
function Fold({
  label,
  testId,
  touch,
  open,
  onToggle,
  children,
}: {
  label: string;
  testId: string;
  touch: boolean;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const contentId = useId();
  return (
    <Box>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={onToggle}
        data-testid={`${testId}-toggle`}
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)', minHeight: touch ? 44 : 32, background: 'none', border: 0, padding: 0, cursor: 'pointer', color: 'var(--gray-12)', font: 'inherit' }}
      >
        <span aria-hidden="true" style={{ display: 'inline-flex' }}>
          <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={16} color="var(--gray-11)" />
        </span>
        <Text size="1" weight="medium">
          {label}
        </Text>
      </button>
      {open ? (
        <Box id={contentId} data-testid={testId} style={{ paddingTop: 'var(--space-1)' }}>
          {children}
        </Box>
      ) : null}
    </Box>
  );
}

function History({ run, touch }: { run: DurableAgentRunHandle; touch: boolean }) {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const [open, setOpen] = useState(false);
  const read = useCallback(() => JobsApi.briefHistory(run, run.runId), [run]);
  const { state, retry } = useRead<JobBriefHistory>(read, open);
  const view = state.status === 'ready' ? briefHistoryView(state.value, words) : null;
  return (
    <Fold label={t('jobPanel.howItChanged')} testId="job-brief-history" touch={touch} open={open} onToggle={() => setOpen((v) => !v)}>
      {state.status === 'loading' ? <Spinner size="1" /> : null}
      {state.status === 'failed' ? <Failed onRetry={retry} /> : null}
      {view ? (
        view.changes.length ? (
          <Rows label={t('jobPanel.howItChanged')} rows={view.changes} />
        ) : (
          <Text size="1" color="gray">
            {t('jobPanel.unchanged')}
          </Text>
        )
      ) : null}
    </Fold>
  );
}

function Failed({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" wrap="wrap" data-testid="job-brief-failed">
      <Text size="1" color="gray">
        {t('jobPanel.briefFailed')}
      </Text>
      <Button size="1" variant="soft" color="gray" onClick={onRetry}>
        {t('jobPanel.tryAgain')}
      </Button>
    </Flex>
  );
}

const LINK_STYLE: React.CSSProperties = { color: 'var(--accent-11)', textDecoration: 'underline', textUnderlineOffset: 2 };
const heading = ({ children }: { children?: React.ReactNode }) => (
  <p style={{ margin: 0, fontWeight: 600 }}>{children}</p>
);
/**
 * The task in the agent's own markdown, read as prose at the brief's size: headings as bold lines (the task is a
 * few sentences, not a document), no images, links only to http(s) and mailto, in a new tab.
 */
const PROSE: Components = {
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>
        {children}
      </a>
    ) : (
      <>{children}</>
    ),
  p: ({ children }) => <p style={{ margin: 0 }}>{children}</p>,
  h1: heading,
  h2: heading,
  h3: heading,
  h4: heading,
  h5: heading,
  h6: heading,
  ul: ({ children }) => <ul style={{ margin: 0, paddingLeft: '1.25em' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: 0, paddingLeft: '1.25em' }}>{children}</ol>,
  pre: ({ children }) => <pre style={{ margin: 0, overflowX: 'auto', fontSize: 'var(--font-size-1)' }}>{children}</pre>,
  code: ({ children }) => <code style={{ fontFamily: 'var(--code-font-family)', fontSize: '0.92em' }}>{children}</code>,
  hr: () => null,
};

function Prose({ text, testId, size = 2 }: { text: string; testId: string; size?: 1 | 2 }) {
  return (
    <div
      data-testid={testId}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-2)',
        fontSize: `var(--font-size-${size})`,
        lineHeight: 1.55,
        color: size === 2 ? 'var(--gray-12)' : 'var(--gray-11)',
        overflowWrap: 'anywhere',
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']} urlTransform={(url) => safeHref(url) ?? ''} components={PROSE}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** Past this many characters the task opens folded to its first lines, with Show more. */
const LONG_TASK = 600;

/** The task: what the person reads first, in a few plain sentences, then their own words and its limits as facts. */
function Task({ task, touch }: { task: BriefTaskView; touch: boolean }) {
  const { t } = useTranslation();
  const [whole, setWhole] = useState(false);
  const contentId = useId();
  if (task.text === null) {
    return (
      <Text as="p" size="2" color="gray" data-testid="job-brief-goal" style={{ margin: 0 }}>
        {t('jobPanel.goalErased')}
      </Text>
    );
  }
  const long = task.text.length > LONG_TASK;
  return (
    <Flex direction="column" gap="2">
      <div id={contentId} style={long && !whole ? { maxHeight: '9.3em', overflow: 'hidden', maskImage: 'linear-gradient(to bottom, black 70%, transparent)' } : undefined}>
        <Prose text={task.text} testId="job-brief-goal" />
      </div>
      {long ? (
        <Button
          size="1"
          variant="ghost"
          color="gray"
          aria-expanded={whole}
          aria-controls={contentId}
          onClick={() => setWhole((v) => !v)}
          data-testid="job-brief-more"
          style={{ alignSelf: 'flex-start', minHeight: touch ? 44 : undefined, margin: 0 }}
        >
          {t(whole ? 'jobPanel.showLess' : 'jobPanel.showMore')}
        </Button>
      ) : null}
      {task.asked ? (
        <Text
          as="p"
          size="1"
          color="gray"
          data-testid="job-brief-asked"
          style={{ margin: 0, overflowWrap: 'anywhere', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
        >
          {t('jobPanel.youAsked', { request: task.asked })}
        </Text>
      ) : null}
      {task.limits.length ? (
        <Flex align="center" gap="1" data-testid="job-brief-limits">
          <span aria-hidden="true" style={{ display: 'inline-flex' }}>
            <MaterialIcon name="schedule" size={14} color="var(--gray-11)" />
          </span>
          <Text size="1" color="gray">
            {task.limits.join(' · ')}
          </Text>
        </Flex>
      ) : null}
    </Flex>
  );
}

/** What it started with: the calls the chat made before handing over, by their plain names, and what earlier work produced. */
function StartedWith({ task, touch }: { task: BriefTaskView; touch: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!task.carried.length && !task.earlier) return null;
  return (
    <Fold label={t('jobPanel.startedWith')} testId="job-brief-started" touch={touch} open={open} onToggle={() => setOpen((v) => !v)}>
      <Flex direction="column" gap="3" style={{ paddingTop: 'var(--space-1)' }}>
        <Rows label={t('jobPanel.alreadyDone')} rows={task.carried} />
        {task.earlier ? (
          <Flex direction="column" gap="1">
            <Text size="1" weight="medium" color="gray">
              {t('jobPanel.earlier')}
            </Text>
            <Prose text={task.earlier} testId="job-brief-earlier" size={1} />
          </Flex>
        ) : null}
      </Flex>
    </Fold>
  );
}

/** A carried call's tool in the activity's plain words: a built-in's action, else its app and name. */
function toolWords(tool: string): string {
  const described = describeTool(tool);
  const label = toolActivityLabel(tool);
  return described.app && !described.key ? `${described.app} · ${label}` : label;
}

/**
 * The job's brief (G4, BGUI-10): its task first, as the design draws it (the task in a few plain sentences, the
 * person's words and its limits as one quiet line), then folds for what it started with, what it was given and how
 * it changed. `touch`: on the phone its folds open from a 44 px row.
 */
export function JobBriefBody({ run, touch = false }: { run: DurableAgentRunHandle; touch?: boolean }) {
  const { t, i18n } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const read = useCallback(() => JobsApi.brief(run, run.runId), [run]);
  const { state, retry } = useRead<JobBrief>(read, true);
  const [given, setGiven] = useState(false);
  if (state.status === 'loading') {
    return (
      <Flex align="center" gap="2" data-testid="job-brief-loading">
        <Spinner size="1" />
        <Text size="1" color="gray">
          {t('jobPanel.briefLoading')}
        </Text>
      </Flex>
    );
  }
  if (state.status === 'failed') return <Failed onRetry={retry} />;
  const view = briefView(state.value, words, { locale: i18n.language, toolWords });
  return (
    <Flex direction="column" gap="3" data-testid="job-brief">
      <Task task={view.task} touch={touch} />
      <Flex direction="column" gap="1">
        <StartedWith task={view.task} touch={touch} />
        {/* What it was given (design `worker-panel.tsx`: "What it was given ▸"): sent, kept back, reach, one click down. */}
        <Fold label={t('jobPanel.whatItWasGiven')} testId="job-brief-given" touch={touch} open={given} onToggle={() => setGiven((v) => !v)}>
          <Flex direction="column" gap="3" style={{ paddingTop: 'var(--space-1)' }}>
            <Rows label={t('jobPanel.sent')} rows={view.sent} />
            <Rows label={t('jobPanel.keptBack')} rows={view.withheld} />
            <Rows label={t('jobPanel.canReach')} rows={view.reach} />
            <Rows label={t('jobPanel.connectedSources')} rows={view.sources} />
          </Flex>
        </Fold>
        <History run={run} touch={touch} />
      </Flex>
    </Flex>
  );
}
