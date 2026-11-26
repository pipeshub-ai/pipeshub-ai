'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Checkbox, Flex, RadioGroup, Text, TextField } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ShareCommonApi } from '@/app/components/share/api';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import type { ShareTeam, ShareUser } from '@/app/components/share/types';
import { LoadState, type Load } from '@/workspace/ai-accounts/components/load-state';
import { ApprovalsDirectory } from '@/workspace/approvals/directory';
import { ConfirmationDialog } from '@/workspace/components/confirmation-dialog';
import { WorkspaceRightPanel, useWorkspaceDrawerNestedModalHost } from '@/workspace/components/workspace-right-panel';
import { formatDateTime } from '@/workspace/harness-ui/format';
import { isHarnessError, refusalText, toHarnessError } from '@/workspace/harness-ui/refusal';
import {
  isLive,
  PUBLICATION_MODES,
  PUBLICATION_REFUSALS,
  PublicationsApi,
  type Publication,
  type PublicationMode,
} from './api';

/** The end of the chosen day in the reader's zone; empty: no expiry. */
export const expiryFromDate = (value: string): number | null => {
  if (!value) return null;
  const at = new Date(`${value}T23:59:59`);
  return Number.isNaN(at.getTime()) ? null : at.getTime();
};

function AudienceSummary({ publication, names }: { publication: Publication; names: Record<string, string> }) {
  const { t } = useTranslation();
  const parts: string[] = [];
  if (publication.audience.org) parts.push(t('publications.audience.org'));
  for (const id of publication.audience.userIds) parts.push(names[id] ?? id);
  for (const id of publication.audience.teamIds) parts.push(names[`team:${id}`] ?? id);
  return <Text size="2">{parts.join(', ')}</Text>;
}

function CreateForm({
  kbId,
  onCreated,
}: {
  kbId: string;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [org, setOrg] = useState(false);
  const [people, setPeople] = useState<ShareUser[]>([]);
  const [teams, setTeams] = useState<ShareTeam[]>([]);
  const [teamIds, setTeamIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [found, setFound] = useState<ShareUser[]>([]);
  const [mode, setMode] = useState<PublicationMode>('answer_only');
  const [expiry, setExpiry] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void ShareCommonApi.listUserTeams({ page: 1, limit: 100 })
      .then(setTeams)
      .catch(() => setTeams([]));
  }, []);

  useEffect(() => {
    const query = search.trim();
    if (query.length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      void fetchShareUsersPaginated({ page: 1, limit: 10, search: query })
        .then(({ users }) => {
          if (live) setFound(users);
        })
        .catch(() => undefined);
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [search]);

  const empty = !org && people.length === 0 && teamIds.size === 0;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await PublicationsApi.create({
        containerKind: 'kb',
        containerId: kbId,
        audience: { org, userIds: people.map((p) => p.id), teamIds: [...teamIds] },
        mode,
        expiresAt: expiryFromDate(expiry),
      });
      setOrg(false);
      setPeople([]);
      setTeamIds(new Set());
      setExpiry('');
      onCreated();
    } catch (failure) {
      setError(refusalText(t, PUBLICATION_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Flex direction="column" gap="3" role="group" aria-label={t('publications.create.title')}>
      <Text size="2" weight="medium">
        {t('publications.create.title')}
      </Text>
      <Text as="label" size="2">
        <Flex gap="2" align="center">
          <Checkbox checked={org} onCheckedChange={(value) => setOrg(value === true)} />
          {t('publications.audience.wholeOrg')}
        </Flex>
      </Text>

      <Flex direction="column" gap="1">
        <Text as="label" htmlFor={`${id}-people`} size="2" weight="medium">
          {t('publications.audience.people')}
        </Text>
        <TextField.Root
          id={`${id}-people`}
          value={search}
          placeholder={t('publications.audience.searchPeople')}
          onChange={(e) => setSearch(e.target.value)}
        />
        {found.length > 0 && (
          <Flex direction="column" gap="1" role="list" aria-label={t('publications.audience.matches')}>
            {found
              .filter((user) => !people.some((p) => p.id === user.id))
              .map((user) => (
                <Box key={user.id} role="listitem">
                  <Button
                    type="button"
                    variant="ghost"
                    style={{ justifyContent: 'flex-start' }}
                    onClick={() => {
                      setPeople((prev) => [...prev, user]);
                      setSearch('');
                    }}
                  >
                    {user.name}
                    {user.email ? ` · ${user.email}` : ''}
                  </Button>
                </Box>
              ))}
          </Flex>
        )}
        {people.length > 0 && (
          <Flex gap="2" wrap="wrap">
            {people.map((person) => (
              <Badge key={person.id} variant="soft">
                {person.name}
                <Button
                  type="button"
                  size="1"
                  variant="ghost"
                  aria-label={t('publications.audience.removePerson', { name: person.name })}
                  onClick={() => setPeople((prev) => prev.filter((p) => p.id !== person.id))}
                >
                  ×
                </Button>
              </Badge>
            ))}
          </Flex>
        )}
      </Flex>

      {teams.length > 0 && (
        <Flex direction="column" gap="1" role="group" aria-label={t('publications.audience.teams')}>
          <Text size="2" weight="medium">
            {t('publications.audience.teams')}
          </Text>
          {teams.map((team) => (
            <Text as="label" size="2" key={team.id}>
              <Flex gap="2" align="center">
                <Checkbox
                  checked={teamIds.has(team.id)}
                  onCheckedChange={(value) =>
                    setTeamIds((prev) => {
                      const next = new Set(prev);
                      if (value === true) next.add(team.id);
                      else next.delete(team.id);
                      return next;
                    })
                  }
                />
                {team.name}
              </Flex>
            </Text>
          ))}
        </Flex>
      )}

      <Flex direction="column" gap="1">
        <Text as="span" id={`${id}-mode`} size="2" weight="medium">
          {t('publications.mode.label')}
        </Text>
        <RadioGroup.Root aria-labelledby={`${id}-mode`} value={mode} onValueChange={(value) => setMode(value as PublicationMode)}>
          {PUBLICATION_MODES.map((value) => (
            <RadioGroup.Item key={value} value={value}>
              <Flex direction="column">
                <Text size="2">{t(`publications.mode.${value}.label`)}</Text>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t(`publications.mode.${value}.hint`)}
                </Text>
              </Flex>
            </RadioGroup.Item>
          ))}
        </RadioGroup.Root>
      </Flex>

      <Flex direction="column" gap="1">
        <Text as="label" htmlFor={`${id}-expiry`} size="2" weight="medium">
          {t('publications.create.expiry')}
        </Text>
        <TextField.Root id={`${id}-expiry`} type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
      </Flex>

      {error && (
        <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
          {error}
        </Text>
      )}
      <Flex>
        <LoadingButton type="button" loading={busy} disabled={empty} onClick={() => void submit()}>
          {t('publications.create.submit')}
        </LoadingButton>
      </Flex>
    </Flex>
  );
}

/**
 * "Publications" of a collection (P2.13): who it is published to, for
 * answers only or in full, until when; publish to more people; revoke.
 */
export function PublicationsPanel({
  open,
  onOpenChange,
  kbId,
  kbName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kbId: string;
  kbName: string;
}) {
  const { t, i18n } = useTranslation();
  const [list, setList] = useState<Load<Publication[]>>({ status: 'loading' });
  const [names, setNames] = useState<Record<string, string>>({});
  const [revoking, setRevoking] = useState<Publication | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modalHost = useWorkspaceDrawerNestedModalHost(open);

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setList({ status: 'loading' });
      try {
        setList({ status: 'ready', data: await PublicationsApi.list('kb', kbId) });
      } catch (failure) {
        setList({ status: 'error', error: isHarnessError(failure) ? failure : toHarnessError(failure) });
      }
    },
    [kbId],
  );

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const ready = list.status === 'ready' ? list.data : null;
  useEffect(() => {
    if (!ready || ready.length === 0) return;
    let live = true;
    const users = [...new Set(ready.flatMap((p) => p.audience.userIds))];
    void Promise.all([
      ApprovalsDirectory.names({ users, groups: [] }).catch(() => ({}) as Record<string, string>),
      ShareCommonApi.listUserTeams({ page: 1, limit: 100 }).catch(() => [] as ShareTeam[]),
    ]).then(([people, teams]) => {
      if (!live) return;
      setNames({ ...people, ...Object.fromEntries(teams.map((team) => [`team:${team.id}`, team.name])) });
    });
    return () => {
      live = false;
    };
  }, [ready]);

  const forbidden = list.status === 'error' && list.error.kind === 'forbidden';
  const now = Date.now();

  const revoke = async () => {
    if (!revoking) return;
    const target = revoking;
    setRevoking(null);
    setBusy(true);
    setError(null);
    try {
      await PublicationsApi.revoke(target.id);
      await load(true);
    } catch (failure) {
      setError(refusalText(t, PUBLICATION_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={t('publications.title', { name: kbName })}
      hideFooter
    >
      <Flex direction="column" gap="5">
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('publications.description')}
        </Text>
        {forbidden ? (
          <Text size="2" role="alert">
            {t('publications.ownersOnly')}
          </Text>
        ) : (
          <>
            <LoadState load={list} unavailableKey="publications.refusal.unavailable" onRetry={() => void load()}>
              {(publications) =>
                publications.length === 0 ? (
                  <Text size="2" style={{ color: 'var(--slate-11)' }}>
                    {t('publications.empty')}
                  </Text>
                ) : (
                  <Flex direction="column" gap="3" role="list" aria-label={t('publications.listLabel')}>
                    {publications.map((publication) => {
                      const live = isLive(publication, now);
                      return (
                        <Flex
                          key={publication.id}
                          direction="column"
                          gap="1"
                          role="listitem"
                          data-testid="publication"
                          style={{ borderBottom: '1px solid var(--olive-3)', paddingBottom: 'var(--space-2)' }}
                        >
                          <AudienceSummary publication={publication} names={names} />
                          <Flex gap="2" align="center" wrap="wrap">
                            <Badge variant="soft">{t(`publications.mode.${publication.mode}.label`)}</Badge>
                            {publication.revokedAt !== null ? (
                              <Badge variant="soft" color="gray">
                                {t('publications.revoked')}
                              </Badge>
                            ) : !live ? (
                              <Badge variant="soft" color="gray">
                                {t('publications.expired')}
                              </Badge>
                            ) : null}
                            <Text size="1" style={{ color: 'var(--slate-11)' }}>
                              {publication.expiresAt !== null
                                ? t('publications.until', { date: formatDateTime(publication.expiresAt, i18n.language) })
                                : t('publications.noExpiry')}
                            </Text>
                          </Flex>
                          {publication.revokedAt === null && (
                            <Flex>
                              <Button type="button" size="1" variant="soft" color="red" disabled={busy} onClick={() => setRevoking(publication)}>
                                {t('publications.revoke')}
                              </Button>
                            </Flex>
                          )}
                        </Flex>
                      );
                    })}
                  </Flex>
                )
              }
            </LoadState>
            {error && (
              <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
                {error}
              </Text>
            )}
            {list.status === 'ready' && <CreateForm kbId={kbId} onCreated={() => void load(true)} />}
          </>
        )}
      </Flex>
      <ConfirmationDialog
        open={revoking !== null}
        onOpenChange={(next) => !next && setRevoking(null)}
        title={t('publications.confirmRevoke.title')}
        message={t('publications.confirmRevoke.message')}
        confirmLabel={t('publications.revoke')}
        container={modalHost}
        onConfirm={() => void revoke()}
      />
    </WorkspaceRightPanel>
  );
}
