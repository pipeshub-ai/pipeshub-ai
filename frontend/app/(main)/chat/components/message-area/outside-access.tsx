'use client';

import React, { useContext, useEffect, useId, useState } from 'react';
import { Box, Button, Card, Flex, Link, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { CloneUnavailableAdminAction, workspacePageLinks } from '@/config';
import { useRunTimeline } from '../../hooks/use-run-timeline';
import type { DurableAgentRunHandle, RepositoryUnavailableView, SiteActivityView } from '../../types';
import { ActivitySectionOpen } from './agent-activity';
import { TARGET } from './approval-card';
import { SecretsHiddenLine } from './secrets-hidden';

/** How many lines show before "Show all" (the approved mock). */
export const SHOWN_LINES = 20;

/** The refusal of a `repository_fetch` whose host or owner the org does not list (WP-63, `repositoriesUnavailable`). */
export const HOST_NOT_ALLOWED = 'host_not_allowed';

export type LineTone = 'ok' | 'held' | 'blocked';

export interface OutsideLine {
  key: string;
  text: string;
  tone: LineTone;
}

/** The server's line with any query string taken off: a page address may carry one, a line never shows it. */
const plain = (label: string): string => label.replace(/\?[^\s:)]*/g, '');

/** The egress proxy's block reasons this build words; any other block reads "Blocked a request to …". */
// The proxy's holds after a private read (SEC-A-01): the person is asked, nothing was refused for good.
const HELD_REASONS: ReadonlySet<string> = new Set(['site_upload_held', 'site_request_held']);
const BLOCK_REASONS: ReadonlySet<string> = new Set([
  'site_read_only',
  'site_relay',
  'site_path_not_allowed',
  'host_mismatch',
  'site_git_refused',
  'destination_not_allowed',
  'destination_private_address',
  'destination_unresolvable',
  'tunnel_ip_literal',
  'tunnel_refused',
  'volume_cap_exceeded',
  'forbidden_pattern',
  'token_replayed',
]);

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

/** A clone's size in the reader's language: "42 MB", "1.3 GB", "800 kB". */
export function humanSize(bytes: number, language: string): string {
  const [value, unit, digits] =
    bytes >= GB ? [bytes / GB, 'gigabyte', 1] : bytes >= MB ? [bytes / MB, 'megabyte', 0] : [Math.max(1, bytes / KB), 'kilobyte', 0];
  try {
    return new Intl.NumberFormat(language, { style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: digits }).format(value);
  } catch {
    return `${value.toFixed(digits)} ${unit === 'gigabyte' ? 'GB' : unit === 'megabyte' ? 'MB' : 'KB'}`;
  }
}

/** `host` and its path (no query; `/` is the site itself), as a line names where. */
function placeOf(site: SiteActivityView): string {
  const path = plain(site.path ?? '');
  return path === '' || path === '/' ? site.host : `${site.host}${path}`;
}

/**
 * One line in the reader's language from the item's `kind` (WP-63); a `kind` this build does not know, or an item
 * from an older server, reads the server's English `label`.
 */
export function lineText(t: TFunction, site: SiteActivityView, language: string): string {
  const at = placeOf(site);
  const host = site.host;
  const access = () =>
    site.access === 'read-write'
      ? t('outsideAccess.access.readWrite')
      : site.access === 'host-level'
        ? t('outsideAccess.access.hostLevel')
        : t('outsideAccess.access.read');
  switch (site.kind) {
    case 'read':
      return t('outsideAccess.line.read', { at, access: access() });
    case 'write':
      return t('outsideAccess.line.write', { at, access: access() });
    case 'host_level':
      return t('outsideAccess.line.hostLevel', { host });
    case 'held':
      return t('outsideAccess.line.held', { at });
    case 'upload_allowed':
      return t('outsideAccess.line.uploadAllowed', { host });
    case 'upload_denied':
      return t('outsideAccess.line.uploadDenied', { host });
    case 'cloned':
      return typeof site.bytes === 'number' && Number.isFinite(site.bytes)
        ? t('outsideAccess.line.cloned', { at, size: humanSize(site.bytes, language) })
        : plain(site.label);
    case 'clone_denied':
      return site.path ? t('outsideAccess.line.cloneDeniedAt', { at }) : t('outsideAccess.line.cloneDenied');
    case 'push_denied':
      return site.path ? t('outsideAccess.line.pushDeniedAt', { at }) : t('outsideAccess.line.pushDenied');
    case 'blocked':
      return site.reason && BLOCK_REASONS.has(site.reason)
        ? t(`outsideAccess.blocked.${site.reason}`, { at })
        : t('outsideAccess.blocked.generic', { at });
    default:
      return plain(site.label);
  }
}

/** The run's outside-access lines, in order: allowed (green), held for the person (amber), blocked or denied (red). */
export function outsideLines(t: TFunction, sites: readonly SiteActivityView[] | undefined, language: string): OutsideLine[] {
  return (sites ?? [])
    .filter((site) => typeof site.host === 'string' && (site.kind !== undefined || (typeof site.label === 'string' && site.label.trim() !== '')))
    .map((site, index) => ({
      key: `${String(index)}-${site.kind ?? ''}-${site.host}-${site.path ?? ''}`,
      text: lineText(t, site, language),
      tone: site.outcome === 'allowed' ? 'ok' : site.kind === 'held' || HELD_REASONS.has(site.reason ?? '') ? 'held' : 'blocked',
    }));
}

const DOT: Record<LineTone, string> = { ok: 'var(--green-9)', held: 'var(--amber-9)', blocked: 'var(--red-9)' };

/** "Outside access (6)": the lines, collapsed until opened, the first 20 then "Show all". Plain words only. */
export function OutsideAccessList({ sites, defaultOpen = false }: { sites: readonly SiteActivityView[] | undefined; defaultOpen?: boolean }) {
  const { t, i18n } = useTranslation();
  const lines = outsideLines(t, sites, i18n.language);
  const [open, setOpen] = useState(defaultOpen);
  const [all, setAll] = useState(false);
  const listId = useId();
  if (lines.length === 0) return null;
  const shown = all ? lines : lines.slice(0, SHOWN_LINES);
  return (
    <Box data-testid="outside-access" style={{ marginTop: 'var(--space-2)', borderLeft: '2px solid var(--olive-5)', paddingLeft: 'var(--space-3)' }}>
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="1"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true" style={{ display: 'inline-flex' }}>
          <MaterialIcon name={open ? 'expand_more' : 'chevron_right'} size={16} />
        </span>
        {t('outsideAccess.title', { n: lines.length })}
      </Button>
      {open ? (
        <Flex id={listId} direction="column" gap="1" asChild style={{ marginTop: 'var(--space-1)' }}>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {shown.map((line) => (
              <li key={line.key} data-tone={line.tone} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline' }}>
                <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: DOT[line.tone], flex: 'none', transform: 'translateY(-1px)' }} />
                <Text size="1" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
                  {line.text}
                </Text>
              </li>
            ))}
          </ul>
        </Flex>
      ) : null}
      {open && !all && lines.length > SHOWN_LINES ? (
        <Button type="button" variant="ghost" size="1" onClick={() => setAll(true)} style={{ marginTop: 'var(--space-1)' }}>
          {t('outsideAccess.showAll', { n: lines.length })}
        </Button>
      ) : null}
    </Box>
  );
}

const sessionOf = (run: DurableAgentRunHandle | null) =>
  run ? { ...(run.agentId ? { agentId: run.agentId } : {}), sessionId: run.sessionId } : null;

/** The run's outside-access lines and what was hidden from its model, at the foot of its steps; read when the steps are first opened (as the summary). */
export function OutsideAccess({ run }: { run: DurableAgentRunHandle | null }) {
  const open = useContext(ActivitySectionOpen);
  const [opened, setOpened] = useState(open);
  useEffect(() => {
    if (open) setOpened(true);
  }, [open]);
  const { timeline } = useRunTimeline(sessionOf(run), run?.runId, { enabled: opened && run !== null });
  if (!timeline || !run || timeline.runId !== run.runId) return null;
  return (
    <>
      <OutsideAccessList sites={timeline.sites} />
      <SecretsHiddenLine hidden={timeline.secretsHidden} />
    </>
  );
}

export interface UnavailableClone {
  host: string;
  path: string;
}

/** Each refused clone once, by host and repository. */
export function unavailableClones(refused: readonly RepositoryUnavailableView[] | undefined): UnavailableClone[] {
  const seen = new Set<string>();
  const out: UnavailableClone[] = [];
  for (const item of refused ?? []) {
    if (item.reason !== HOST_NOT_ALLOWED || typeof item.host !== 'string' || item.host === '') continue;
    const path = plain(typeof item.path === 'string' ? item.path : '');
    const key = `${item.host}${path}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ host: item.host, path });
  }
  return out;
}

/**
 * "Clone not available" (WP-63): the org does not list the repository's host. An org admin gets the edition's action
 * (`CloneUnavailableAdminAction`) or, where this build has a page that lists git hosts, a pointer to it; anyone else
 * gets the note to send an admin, with Copy. Without such a page the card only says why the clone did not run.
 */
export function CloneUnavailableCard({ clone, isAdmin }: { clone: UnavailableClone; isAdmin: boolean }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const gitHostsPage = workspacePageLinks['git-hosts'] ?? null;
  const askNote = t('outsideAccess.unavailable.askAdmin', { host: clone.host });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(askNote);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card data-testid="clone-unavailable" role="region" aria-label={t('outsideAccess.unavailable.title')} style={{ marginTop: 'var(--space-2)', borderLeft: '4px solid var(--red-9)' }}>
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2">
          <MaterialIcon name="block" size={18} color="var(--red-11)" />
          <Text size="2" weight="bold">
            {t('outsideAccess.unavailable.title')}
          </Text>
        </Flex>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('outsideAccess.unavailable.body', { host: clone.host })}
        </Text>
        {isAdmin ? (
          <CloneUnavailableAdminAction clone={clone}>
            {gitHostsPage === null ? null : (
              <Text size="2">
                {t('outsideAccess.unavailable.adminAdd')}{' '}
                <Link href={gitHostsPage}>{t('outsideAccess.unavailable.adminLink')}</Link>
              </Text>
            )}
          </CloneUnavailableAdminAction>
        ) : gitHostsPage === null ? null : (
          <Flex gap="2" align="center" wrap="wrap">
            <Text size="2" data-testid="clone-unavailable-ask" style={{ flex: '1 1 14rem' }}>
              {askNote}
            </Text>
            <Button type="button" variant="soft" color="gray" style={TARGET} onClick={() => void copy()}>
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon name={copied ? 'check' : 'content_copy'} size={16} />
              </span>
              {copied ? t('outsideAccess.unavailable.copied') : t('outsideAccess.unavailable.copy')}
            </Button>
          </Flex>
        )}
      </Flex>
    </Card>
  );
}

/** The latest answer's refused clones, as cards (read once the run ended). */
export function CloneUnavailableCards({ run }: { run: DurableAgentRunHandle | null }) {
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const { timeline } = useRunTimeline(sessionOf(run), run?.runId, { enabled: run !== null });
  if (!timeline || !run || timeline.runId !== run.runId) return null;
  const clones = unavailableClones(timeline.repositoriesUnavailable);
  if (clones.length === 0) return null;
  return (
    <Box>
      {clones.map((clone) => (
        <CloneUnavailableCard key={`${clone.host}${clone.path}`} clone={clone} isAdmin={isAdmin} />
      ))}
    </Box>
  );
}
