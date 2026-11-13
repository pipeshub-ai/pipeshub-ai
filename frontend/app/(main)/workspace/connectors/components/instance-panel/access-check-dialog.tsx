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
import { ConnectorsApi } from '../../api';
import type { AccessExplanation, AccessExplanationCode } from '../../types';

const MAX_PEOPLE = 100;
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
  | { kind: 'done'; who: string; recordId: string; result: AccessExplanation }
  | { kind: 'error'; reason: 'failed' | 'notFound' | 'rateLimited' };

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
  const [recordId, setRecordId] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const resultHeading = useRef<HTMLHeadingElement>(null);
  // Each check and each edit of the inputs takes a new number; a response that is not for the latest one is dropped.
  const latest = useRef(0);

  // The instance switcher reuses this component: another connector starts clean, and an answer still on its way for the last one is dropped.
  useEffect(() => {
    latest.current += 1;
    setPhase({ kind: 'idle' });
    setUserId('');
    setRecordId('');
  }, [connectorId]);

  useEffect(() => {
    if (phase.kind === 'done' || phase.kind === 'error') resultHeading.current?.focus();
  }, [phase]);

  const canCheck = userId !== '' && recordId.trim() !== '' && phase.kind !== 'loading';

  const check = useCallback(async () => {
    const who = people?.find((u) => u.userId === userId);
    const asked = recordId.trim();
    const mine = ++latest.current;
    setPhase({ kind: 'loading' });
    try {
      const result = await ConnectorsApi.explainAccess(connectorId, userId, asked);
      if (mine === latest.current) setPhase({ kind: 'done', who: who ? userLabel(who) : '', recordId: asked, result });
    } catch (err: unknown) {
      if (mine !== latest.current) return;
      const status = isProcessedError(err) ? err.statusCode : undefined;
      setPhase({ kind: 'error', reason: status === 404 ? 'notFound' : status === 429 ? 'rateLimited' : 'failed' });
    }
  }, [connectorId, people, recordId, userId]);

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
                value={recordId}
                maxLength={256}
                aria-describedby="access-check-record-hint"
                onChange={(e) => changeInputs(() => setRecordId(e.target.value))}
              />
              <Text id="access-check-record-hint" size="1" color="gray">
                {t('workspace.connectors.accessCheck.recordHint')}
              </Text>
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
                    color={phase.result.admitted ? 'var(--jade-9)' : 'var(--red-9)'}
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
                {t('workspace.connectors.accessCheck.askedAbout', { record: phase.recordId })}
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
