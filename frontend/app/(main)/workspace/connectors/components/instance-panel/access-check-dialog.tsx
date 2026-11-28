'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, Heading, Select, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { isProcessedError } from '@/lib/api/api-error';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import type { User } from '@/app/(main)/workspace/users/types';
import { KnowledgeHubApi } from '@/knowledge-base/api';
import { KB_MIN_SEARCH_QUERY_LENGTH } from '@/knowledge-base/utils';
import { ConnectorsApi } from '../../api';
import type { AccessExplanation, AccessExplanationCode } from '../../types';

const MAX_PEOPLE = 100;
const RECORD_SEARCH_LIMIT = 10;
const RECORD_SEARCH_DEBOUNCE_MS = 250;
const NEXT_STEP: Partial<Record<AccessExplanationCode, true>> = {
  not_linked: true,
  no_grant: true,
  restricted_container: true,
  item_restricted: true,
  parent_not_readable: true,
  pending_rewrite: true,
  syncing: true,
  scheme_unreadable: true,
  deleted_at_source: true,
  unexplained: true,
};

type Phase =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'done'; who: string; record: string; result: AccessExplanation }
  | { kind: 'error'; reason: 'failed' | 'notFound' | 'rateLimited' };

interface RecordHit {
  id: string;
  name: string;
  /** The folder or group it sits in, to tell same-named records apart. */
  where?: string;
}

type RecordSearch = { kind: 'idle' } | { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; hits: RecordHit[] };

export interface AccessCheckDialogProps {
  connectorId: string;
  /** Portal host from the drawer, so the dialog stacks above it. */
  container?: HTMLElement | null;
}

function userLabel(u: User): string {
  return u.name || u.email || u.userId;
}

/** Org admins only (the caller decides): whether one person can read one record of a connector, and why not. */
export function AccessCheckDialog({ connectorId, container }: AccessCheckDialogProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Read only while the dialog is open; a failed read is asked again by `mutate`.
  const {
    data: peopleData,
    error: peopleError,
    mutate: retryPeople,
  } = useSWR(
    open ? ([SWR_NS.userDirectory, 'access-check-people'] as const) : null,
    // The dialog words a failed load itself, so the request raises no toast of its own.
    () => UsersApi.listUsers({ limit: MAX_PEOPLE }, { suppressErrorToast: true }),
    SWR_POLICY.list60s
  );
  const people: User[] | null = peopleData?.users ?? null;
  const peopleFailed = peopleError !== undefined;
  const [userId, setUserId] = useState('');
  // What the admin typed: a name to search for, or a pasted record id.
  const [recordQuery, setRecordQuery] = useState('');
  const [picked, setPicked] = useState<RecordHit | null>(null);
  const [search, setSearch] = useState<RecordSearch>({ kind: 'idle' });
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const resultHeading = useRef<HTMLHeadingElement>(null);
  // Each check and each edit of the inputs takes a new number; a response that is not for the latest one is dropped.
  const latest = useRef(0);

  // The instance switcher reuses this component: another connector starts clean, and an answer still on its way for the last one is dropped.
  useEffect(() => {
    latest.current += 1;
    setPhase({ kind: 'idle' });
    setUserId('');
    setRecordQuery('');
    setPicked(null);
  }, [connectorId]);

  // Searches this connector's records by name. It finds only what the admin can read; any other record is checked by its id.
  useEffect(() => {
    const q = recordQuery.trim();
    if (picked || q.length < KB_MIN_SEARCH_QUERY_LENGTH) {
      setSearch({ kind: 'idle' });
      return;
    }
    setSearch({ kind: 'loading' });
    const controller = new AbortController();
    const timer = setTimeout(() => {
      KnowledgeHubApi.searchNodes({
        q,
        parent: { nodeType: 'app', id: connectorId },
        // Records only, so folders that match the name do not fill the page.
        nodeTypes: 'record',
        limit: RECORD_SEARCH_LIMIT,
        signal: controller.signal,
      })
        .then((res) => {
          // A late page for an earlier query must not replace the list for the text now in the field.
          if (controller.signal.aborted) return;
          const hits = res.items
            .filter((n) => n.nodeType === 'record')
            .map((n) => ({ id: n.id, name: n.name, where: n.parent?.name }));
          setSearch({ kind: 'ready', hits });
        })
        .catch(() => {
          if (!controller.signal.aborted) setSearch({ kind: 'error' });
        });
    }, RECORD_SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [connectorId, picked, recordQuery]);

  useEffect(() => {
    if (phase.kind === 'done' || phase.kind === 'error') resultHeading.current?.focus();
  }, [phase]);

  const recordId = picked?.id ?? recordQuery.trim();
  const canCheck = userId !== '' && recordId !== '' && phase.kind !== 'loading';

  const check = useCallback(async () => {
    const who = people?.find((u) => u.userId === userId);
    const record = picked?.name ?? recordId;
    const mine = ++latest.current;
    setPhase({ kind: 'loading' });
    try {
      const result = await ConnectorsApi.explainAccess(connectorId, userId, recordId);
      if (mine === latest.current) setPhase({ kind: 'done', who: who ? userLabel(who) : '', record, result });
    } catch (err: unknown) {
      if (mine !== latest.current) return;
      const status = isProcessedError(err) ? err.statusCode : undefined;
      setPhase({ kind: 'error', reason: status === 404 ? 'notFound' : status === 429 ? 'rateLimited' : 'failed' });
    }
  }, [connectorId, people, picked, recordId, userId]);

  const changeInputs = (update: () => void): void => {
    latest.current += 1;
    setPhase({ kind: 'idle' });
    update();
  };

  const container_ = (r: AccessExplanation): string =>
    r.container?.name ?? t('workspace.connectors.accessCheck.restrictedArea');

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger>
        <Button type="button" variant="soft" color="gray" size="2">
          {t('workspace.connectors.accessCheck.open')}
        </Button>
      </Dialog.Trigger>
      <Dialog.Content container={container ?? undefined} style={{ maxWidth: '37.5rem' }}>
        <Dialog.Title>{t('workspace.connectors.accessCheck.title')}</Dialog.Title>
        <Dialog.Description size="2" color="gray">
          {t('workspace.connectors.accessCheck.description')}
        </Dialog.Description>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canCheck) void check();
          }}
        >
          <Flex direction="column" gap="3" mt="4">
            <Flex direction="column" gap="1">
              <Text as="label" size="2" weight="medium" id="access-check-person">
                {t('workspace.connectors.accessCheck.personLabel')}
              </Text>
              <Select.Root value={userId} onValueChange={(v) => changeInputs(() => setUserId(v))} disabled={people === null}>
                <Select.Trigger
                  aria-labelledby="access-check-person"
                  placeholder={
                    peopleFailed
                      ? t('workspace.connectors.accessCheck.peopleFailed')
                      : people === null
                        ? t('workspace.connectors.accessCheck.peopleLoading')
                        : t('workspace.connectors.accessCheck.personPlaceholder')
                  }
                />
                <Select.Content container={container ?? undefined}>
                  {(people ?? []).map((u) => (
                    <Select.Item key={u.userId} value={u.userId}>
                      {userLabel(u)}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
              {peopleFailed ? (
                <Button type="button" variant="soft" size="1" style={{ alignSelf: 'flex-start' }} onClick={() => void retryPeople()}>
                  {t('workspace.connectors.accessCheck.peopleRetry')}
                </Button>
              ) : null}
            </Flex>

            <Flex direction="column" gap="1">
              <Text as="label" size="2" weight="medium" htmlFor="access-check-record">
                {t('workspace.connectors.accessCheck.recordLabel')}
              </Text>
              <TextField.Root
                id="access-check-record"
                value={recordQuery}
                maxLength={256}
                autoComplete="off"
                aria-describedby="access-check-record-hint"
                onChange={(e) =>
                  changeInputs(() => {
                    setRecordQuery(e.target.value);
                    setPicked(null);
                  })
                }
              >
                <TextField.Slot>
                  <MaterialIcon name="search" size={16} />
                </TextField.Slot>
              </TextField.Root>
              <Text id="access-check-record-hint" size="1" color="gray">
                {t('workspace.connectors.accessCheck.recordHint')}
              </Text>
              {/* Always mounted, so a screen reader announces each change; not a `status`, which belongs to the check. */}
              <Text aria-live="polite" size="1" color="gray">
                {search.kind === 'loading'
                  ? t('workspace.connectors.accessCheck.recordSearching')
                  : search.kind === 'error'
                    ? t('workspace.connectors.accessCheck.recordSearchFailed')
                    : search.kind === 'ready' && search.hits.length === 0
                      ? t('workspace.connectors.accessCheck.recordNoMatches')
                      : null}
              </Text>
              {search.kind === 'ready' && search.hits.length > 0 ? (
                <Flex
                  role="group"
                  aria-label={t('workspace.connectors.accessCheck.recordMatches')}
                  direction="column"
                  gap="1"
                  style={{ maxHeight: '12rem', overflowY: 'auto' }}
                >
                  {search.hits.map((hit) => (
                    <Button
                      key={hit.id}
                      type="button"
                      variant="soft"
                      color="gray"
                      style={{ justifyContent: 'flex-start', height: 'auto', minHeight: '44px', paddingBlock: 'var(--space-1)' }}
                      onClick={() =>
                        changeInputs(() => {
                          setPicked(hit);
                          setRecordQuery(hit.name);
                        })
                      }
                    >
                      <Flex direction="column" align="start">
                        <Text size="2">{hit.name}</Text>
                        {hit.where ? (
                          <Text size="1" color="gray">
                            {hit.where}
                          </Text>
                        ) : null}
                      </Flex>
                    </Button>
                  ))}
                </Flex>
              ) : null}
            </Flex>
          </Flex>

          <Flex justify="end" gap="2" mt="4">
            <Dialog.Close>
              <Button type="button" variant="soft" color="gray">
                {t('workspace.connectors.accessCheck.close')}
              </Button>
            </Dialog.Close>
            <Button type="submit" disabled={!canCheck}>
              {t('workspace.connectors.accessCheck.check')}
            </Button>
          </Flex>
        </form>

        <Flex direction="column" gap="2" mt="4" aria-live="polite">
          {phase.kind === 'loading' ? (
            <Text role="status" size="2" color="gray">
              {t('workspace.connectors.accessCheck.checking')}
            </Text>
          ) : null}

          {phase.kind === 'error' ? (
            <>
              <Heading as="h3" size="3" tabIndex={-1} ref={resultHeading} style={{ outline: 'none' }}>
                {t('workspace.connectors.accessCheck.resultHeading')}
              </Heading>
              <Text role="alert" size="2" color="red">
                {t(`workspace.connectors.accessCheck.errors.${phase.reason}`)}
              </Text>
            </>
          ) : null}

          {phase.kind === 'done' ? (
            <>
              <Heading as="h3" size="3" tabIndex={-1} ref={resultHeading} style={{ outline: 'none' }}>
                <Flex align="center" gap="2">
                  <MaterialIcon
                    name={phase.result.admitted ? 'check_circle' : 'block'}
                    size={20}
                    color={phase.result.admitted ? 'var(--jade-11)' : 'var(--red-11)'}
                  />
                  {t(
                    phase.result.admitted
                      ? 'workspace.connectors.accessCheck.verdict.granted'
                      : 'workspace.connectors.accessCheck.verdict.denied',
                    { name: phase.who || t('workspace.connectors.accessCheck.theyFallback') }
                  )}
                </Flex>
              </Heading>
              <Text size="1" color="gray">
                {t('workspace.connectors.accessCheck.askedAbout', { record: phase.record })}
              </Text>
              <Text size="2">
                {t(`workspace.connectors.accessCheck.reason.${phase.result.code}`, {
                  name: phase.who || t('workspace.connectors.accessCheck.theyFallback'),
                  container: container_(phase.result),
                })}
              </Text>
              {NEXT_STEP[phase.result.code] ? (
                <Text size="2" color="gray">
                  {t(`workspace.connectors.accessCheck.next.${phase.result.code}`, {
                    name: phase.who || t('workspace.connectors.accessCheck.theyFallback'),
                  })}
                </Text>
              ) : null}
              {phase.result.path.length > 0 ? (
                <Text size="1" color="gray">
                  {t('workspace.connectors.accessCheck.pathLabel', {
                    path: phase.result.path
                      .map((c) => c.name ?? t('workspace.connectors.accessCheck.restrictedArea'))
                      .join(' / '),
                  })}
                </Text>
              ) : null}
              <Text size="1" color="gray">
                {t('workspace.connectors.accessCheck.reference', { ref: phase.result.reference })}
              </Text>
            </>
          ) : null}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
