'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { Button, Flex, Popover, Select, Switch, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { CollaborationApi, conversationErrorStatus } from '../../collaboration-api';
import {
  isCollaboratorsView,
  type AccessChange,
  type ConversationRef,
  type ExplainResponse,
} from '../../collaboration-types';
import { ProjectApi } from '../../project-api';
import { describeExplainPath } from '../../utils/explain-sentences';
import { resolvePrincipalNames } from '../../utils/principal-names';
import { AccessChangeDialog } from './access-change-dialog';

export interface AccessPanelProps {
  conversationRef: ConversationRef;
  /** The caller owns the chat (server `access.isOwner`). Owners may explain others and switch visibility. */
  isOwner: boolean;
  /** Set when the chat is linked to a project; enables the owner's visibility switch. */
  project?: { projectId: string; visibility: 'private' | 'project' } | null;
  /** Called after the visibility change was applied. */
  onVisibilityChanged?: (visibility: 'private' | 'project') => void;
}

type ExplainState =
  | { status: 'loading' }
  | { status: 'ready'; explain: ExplainResponse; teamNames: ReadonlyMap<string, string> }
  | { status: 'error'; forbidden: boolean };

interface SubjectOption {
  userId: string;
  name: string;
}

/**
 * "Access" button with a popover that says why the caller (or, for the owner and org admins, a
 * chosen person) can open this chat. Renders nothing with the collaboration flag off.
 */
/** Radix Select items cannot carry an empty value. */
const SELF_VALUE = '__self__';

export function AccessPanel({ conversationRef, isOwner, project, onVisibilityChanged }: AccessPanelProps) {
  const { t } = useTranslation();
  const enabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const myUserId = useUserStore((s) => s.profile?.userId);
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState('');
  const [options, setOptions] = useState<SubjectOption[]>([]);
  const [state, setState] = useState<ExplainState>({ status: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [pendingChange, setPendingChange] = useState<AccessChange | null>(null);
  const [visibility, setVisibility] = useState(project?.visibility ?? 'private');
  const selectId = useId();
  const refKey = JSON.stringify(conversationRef);
  const canPickSubject = isOwner || isAdmin;

  useEffect(() => setVisibility(project?.visibility ?? 'private'), [project?.visibility]);
  useEffect(() => setSubject(''), [refKey]);

  useEffect(() => {
    if (!enabled || !open || !canPickSubject) return;
    let cancelled = false;
    CollaborationApi.getCollaborators(conversationRef)
      .then((response) => {
        if (cancelled || !isCollaboratorsView(response)) return;
        setOptions(
          response.collaborators
            .filter((c) => c.principalType === 'user' && c.state === 'active' && c.principalId !== myUserId)
            .map((c) => ({ userId: c.principalId, name: c.displayName })),
        );
      })
      .catch(() => {
        if (!cancelled) setOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, open, canPickSubject, refKey, myUserId]);

  useEffect(() => {
    if (!enabled || !open) return;
    const controller = new AbortController();
    setState({ status: 'loading' });
    (async () => {
      try {
        const explain = await CollaborationApi.explain(conversationRef, subject || undefined, controller.signal);
        const teamIds = explain.via.flatMap((p) => (p.type === 'team' && p.ref ? [p.ref] : []));
        const names = teamIds.length ? await resolvePrincipalNames(conversationRef, { teamIds }) : null;
        if (!controller.signal.aborted) {
          setState({ status: 'ready', explain, teamNames: names?.teams ?? new Map() });
        }
      } catch (error) {
        if (!controller.signal.aborted) setState({ status: 'error', forbidden: conversationErrorStatus(error) === 403 });
      }
    })();
    return () => controller.abort();
  }, [enabled, open, subject, reloadKey, refKey]);

  const subjectName = useMemo(
    () => (subject ? options.find((o) => o.userId === subject)?.name || t('chat.collab.access.unknownUser') : undefined),
    [subject, options, t],
  );

  if (!enabled) return null;

  // The owner's own access is the ownership; a team that also happens to include them is noise.
  const visiblePaths =
    state.status !== 'ready'
      ? []
      : isOwner && !subject
        ? state.explain.via.filter((p) => p.type !== 'team')
        : state.explain.via;

  const requestVisibility = (next: boolean) => {
    setOpen(false);
    setPendingChange({ type: 'visibility', visibility: next ? 'project' : 'private' });
  };

  const applyVisibility = async () => {
    if (pendingChange?.type !== 'visibility') return;
    await ProjectApi.setConversationProjectVisibility(
      conversationRef.id,
      pendingChange.visibility,
      conversationRef.kind === 'agent' ? { agentKey: conversationRef.agentKey } : {},
    );
    setVisibility(pendingChange.visibility);
    onVisibilityChanged?.(pendingChange.visibility);
  };

  return (
    <>
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger>
          <Button
            size="1"
            variant="ghost"
            color="gray"
            aria-haspopup="dialog"
            aria-label={t('chat.collab.access.buttonLabel')}
            style={{ height: 20, padding: '0 var(--space-2)', gap: 'var(--space-1)' }}
          >
            <MaterialIcon name="lock_person" size={14} color="var(--slate-11)" />
            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('chat.collab.access.button')}
            </Text>
          </Button>
        </Popover.Trigger>
        <Popover.Content
          size="2"
          style={{ width: 'min(360px, calc(100vw - 32px))' }}
          aria-label={t('chat.collab.access.title')}
          role="dialog"
        >
          <Flex direction="column" gap="3">
            <Text as="p" size="3" weight="medium">
              {t('chat.collab.access.title')}
            </Text>

            {canPickSubject && (
              <Flex direction="column" gap="1">
                <Text as="label" size="1" htmlFor={selectId} style={{ color: 'var(--slate-11)' }}>
                  {t('chat.collab.access.subjectLabel')}
                </Text>
                <Select.Root
                  value={subject || SELF_VALUE}
                  onValueChange={(v) => setSubject(v === SELF_VALUE ? '' : v)}
                >
                  <Select.Trigger id={selectId} aria-label={t('chat.collab.access.subjectLabel')} style={{ width: '100%' }} />
                  <Select.Content position="popper">
                    <Select.Item value={SELF_VALUE}>{t('chat.collab.access.subjectMe')}</Select.Item>
                    {options.map((o) => (
                      <Select.Item key={o.userId} value={o.userId}>
                        {o.name}
                      </Select.Item>
                    ))}
                  </Select.Content>
                </Select.Root>
              </Flex>
            )}

            <div aria-live="polite" role="status" data-testid="access-explain">
              {state.status === 'loading' && (
                <Flex align="center" gap="2">
                  <Spinner size={14} />
                  <Text size="2">{t('chat.collab.access.loading')}</Text>
                </Flex>
              )}
              {state.status === 'error' && (
                <Flex direction="column" gap="2" align="start">
                  <Text size="2" color="red">
                    {t(state.forbidden ? 'chat.collab.access.forbidden' : 'chat.collab.access.loadFailed')}
                  </Text>
                  {!state.forbidden && (
                    <Button size="1" variant="soft" onClick={() => setReloadKey((k) => k + 1)}>
                      {t('chat.collab.access.retry')}
                    </Button>
                  )}
                </Flex>
              )}
              {state.status === 'ready' && (
                <Flex direction="column" gap="2">
                  <Text as="p" size="2" weight="medium">
                    {t(subjectName ? 'chat.collab.access.summaryOther' : 'chat.collab.access.summarySelf', {
                      name: subjectName,
                      role: t(`chat.collab.access.role.${state.explain.role}`),
                    })}
                  </Text>
                  {visiblePaths.length === 0 ? (
                    <Text as="p" size="2">
                      {t(subjectName ? 'chat.collab.access.noneOther' : 'chat.collab.access.noneSelf', { name: subjectName })}
                    </Text>
                  ) : (
                    <ul style={{ margin: 0, paddingLeft: 'var(--space-4)' }}>
                      {visiblePaths.map((path, i) => {
                        const sentence = describeExplainPath(path, t, { subjectName, teamNames: state.teamNames });
                        return (
                          <li key={`${path.type}:${path.ref ?? 'redacted'}:${i}`}>
                            <Text size="2">{t(sentence.key, sentence.vars)}</Text>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </Flex>
              )}
            </div>

            {isOwner && project && (
              <Flex direction="column" gap="1" style={{ borderTop: '1px solid var(--slate-5)', paddingTop: 'var(--space-3)' }}>
                <Flex align="center" justify="between" gap="3">
                  <Text as="label" size="2" id={`${selectId}-vis`}>
                    {t('chat.collab.access.visibilityLabel')}
                  </Text>
                  <Switch
                    size="1"
                    checked={visibility === 'project'}
                    onCheckedChange={requestVisibility}
                    aria-labelledby={`${selectId}-vis`}
                    aria-describedby={`${selectId}-vis-help`}
                  />
                </Flex>
                <Text id={`${selectId}-vis-help`} size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('chat.collab.access.visibilityHelp')}
                </Text>
              </Flex>
            )}
          </Flex>
        </Popover.Content>
      </Popover.Root>

      <AccessChangeDialog
        change={pendingChange}
        conversationRef={conversationRef}
        onApply={applyVisibility}
        onClose={() => setPendingChange(null)}
      />
    </>
  );
}
