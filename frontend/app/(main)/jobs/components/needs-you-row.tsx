'use client';

import { Button, Flex, Text } from '@radix-ui/themes';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget } from '@/app/components/ui/responsive';
import { Link } from '@/lib/navigation';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import { inboxRow } from '@/lib/harness-jobs/inbox';
import type { InboxRowAction } from '@/lib/harness-jobs/view-models';
import { chatAtHref } from '../url-params';
import { approveSkill, editSkill, rejectSkill } from './needs-you-actions';

/** The job an ask came from, when it came from one: the row names it, and its link opens that job's panel too. */
export interface AskJob {
  title: string;
  chatTitle?: string;
}

const TONE_COLOR: Record<string, string> = {
  accent: 'var(--amber-11)',
  warning: 'var(--amber-11)',
  danger: 'var(--red-11)',
  quiet: 'var(--gray-11)',
};

const BUTTON_COLOR: Record<InboxRowAction['weight'], { variant: 'solid' | 'soft' | 'outline'; color: 'gray' | 'red' | undefined }> = {
  primary: { variant: 'solid', color: undefined },
  secondary: { variant: 'soft', color: 'gray' },
  no: { variant: 'soft', color: 'red' },
};

/** Where an ask is answered: its chat, at its card (D9), with the job's panel open when a job asked. */
export function askHref(item: InboxItem, job: AskJob | null): string | undefined {
  if (item.pointer.type !== 'conversation') return item.link && item.link !== '/' ? item.link : undefined;
  return chatAtHref({
    conversationId: item.pointer.conversationId,
    agentKey: item.pointer.agentKey,
    jobRunId: job ? item.pointer.runId : null,
    askId: item.pointer.interactionId,
  });
}

/**
 * One ask waiting on the person: what it is in words (never a tool id), where it came from, how long ago, and the
 * one way to settle it, in the chat for approvals and questions, in place for a skill proposal.
 */
export function NeedsYouRow({ item, job, now }: { item: InboxItem; job: AskJob | null; now: number }) {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const isMobile = useIsMobile();
  const row = inboxRow(item, now, t, i18n.language);
  const runId = 'runId' in item.pointer ? item.pointer.runId : undefined;
  const interactionId = item.pointer.type === 'conversation' ? item.pointer.interactionId : undefined;
  const meta = [job?.title, job?.chatTitle, row.time].filter(Boolean).join(' · ');
  const leaveTo = askHref(item, job);

  const act = (action: InboxRowAction) => {
    if (action.id === 'approveSkill') void approveSkill(item, row.title, t);
    else if (action.id === 'rejectSkill') void rejectSkill(item, row.title, t);
    else if (action.id === 'editSkill') void editSkill(item, (href) => router.push(href), t);
  };

  return (
    <Flex
      role="listitem"
      data-testid="needs-you-row"
      {...(runId ? { 'data-run-id': runId } : {})}
      {...(interactionId ? { 'data-interaction-id': interactionId } : {})}
      direction={isMobile ? 'column' : 'row'}
      align={isMobile ? 'stretch' : 'center'}
      gap="3"
      style={{
        minHeight: 44,
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--amber-6)',
        backgroundColor: 'var(--color-panel-solid)',
        boxSizing: 'border-box',
      }}
    >
      <Flex align="start" gap="3" style={{ flex: 1, minWidth: 0 }}>
        <MaterialIcon name={row.icon ?? 'pan_tool'} size={18} color={TONE_COLOR[row.tone ?? 'accent']} />
        <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
            {row.title}
          </Text>
          {row.description && (
            <Text size="1" color="gray" style={{ overflowWrap: 'anywhere' }}>
              {row.description}
            </Text>
          )}
          <Text size="1" color="gray">
            {meta}
          </Text>
        </Flex>
      </Flex>
      <Flex align="center" justify="end" gap="2" wrap="wrap" style={{ flexShrink: 0 }}>
        {(row.actions ?? []).map((action) => {
          const look = BUTTON_COLOR[action.weight];
          const style = { margin: 0, ...touchTarget(isMobile) };
          if ((action.id === 'openAsk' || action.id === 'open') && leaveTo)
            return (
              <Button key={action.id} asChild size="1" variant={look.variant} color={look.color} style={style}>
                <Link href={leaveTo}>{action.label}</Link>
              </Button>
            );
          if (action.leaves && action.id !== 'editSkill') return null;
          return (
            <Button key={action.id} size="1" variant={look.variant} color={look.color} onClick={() => act(action)} style={style}>
              {action.label}
            </Button>
          );
        })}
      </Flex>
    </Flex>
  );
}
