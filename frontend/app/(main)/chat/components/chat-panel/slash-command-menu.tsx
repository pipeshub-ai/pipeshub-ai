'use client';

import React, { useRef, useState } from 'react';
import { Badge, Box, Button, Flex, Popover, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useActiveOptionInView } from '@/lib/hooks/use-active-option-in-view';
import { argumentHint, type RenderResult } from '@/lib/commands/render';
import type { CommandGroup, CommandOwner, CommandProvenance, ComposerCommandState, MenuEntry } from '@/chat/commands';
import type { BuiltinCommand, BuiltinReason } from '@/chat/builtin-commands';

export const SLASH_MENU_ID = 'slash-command-menu';
export const slashOptionId = (name: string): string => `slash-command-option-${name}`;

/** Literal keys per owner, so `i18n:keys` checks each one. */
export function ownerLabel(t: TFunction, owner: CommandOwner): string {
  switch (owner) {
    case 'pipeshub':
      return t('slashCommands.owner.pipeshub');
    case 'team':
      return t('slashCommands.owner.team');
    case 'org':
      return t('slashCommands.owner.org');
    default:
      return t('slashCommands.owner.you');
  }
}

/** "by Asha · team Ops" for a shared command; null for yours and PipesHub's (their badge says it). */
export function sharedByText(t: TFunction, command: CommandProvenance): string | null {
  if (command.owner !== 'team' && command.owner !== 'org') return null;
  const author = command.author
    ? t('slashCommands.byAuthor', { name: command.author })
    : command.authorFormer
      ? t('slashCommands.byFormerMember')
      : null;
  const team = command.owner === 'team' && command.team ? t('slashCommands.teamNamed', { team: command.team }) : null;
  return [author, team].filter(Boolean).join(' · ') || null;
}

export function OwnerBadge({ owner }: { owner: CommandOwner }) {
  const { t } = useTranslation();
  return (
    <Badge size="1" variant="soft" color={owner === 'pipeshub' ? 'jade' : 'gray'} style={{ flexShrink: 0 }}>
      {ownerLabel(t, owner)}
    </Badge>
  );
}

/** Literal keys per group, so `i18n:keys` checks each one. */
export function groupLabel(t: TFunction, group: CommandGroup): string {
  switch (group) {
    case 'builtin':
      return t('slashCommands.group.builtin');
    case 'sandbox':
      return t('slashCommands.group.sandbox');
    case 'team':
      return t('slashCommands.group.team');
    case 'org':
      return t('slashCommands.group.org');
    default:
      return t('slashCommands.group.you');
  }
}

/** A built-in's one-line description; the key comes from the registry (`<i18n>.description`). */
export function builtinDescription(t: TFunction, command: BuiltinCommand): string {
  return t(`${command.i18n}.description`);
}

/** What a reason's words name: `/repo`'s repository (`owner/name`). */
export interface BuiltinReasonValues {
  repository?: string;
}

/** Why a built-in is greyed out here, in one line (O8). Literal keys, so `i18n:keys` checks them. */
export function builtinReasonText(
  t: TFunction,
  command: BuiltinCommand,
  reason: BuiltinReason,
  values: BuiltinReasonValues = {},
): string {
  switch (reason) {
    case 'repoOrgOff':
      return t('slashCommands.builtin.repo.reason.orgOff');
    case 'repoNoEngine':
      return t('slashCommands.builtin.repo.reason.noEngine');
    case 'repoTaken':
      return t('slashCommands.builtin.repo.reason.hasRepository', { repo: values.repository ?? '' });
    case 'notRunning':
      return t('slashCommands.reason.notRunning');
    case 'wait':
      return t('slashCommands.reason.wait');
    case 'noConversation':
      return t('slashCommands.reason.noConversation');
    case 'noAnswer':
      return t('slashCommands.reason.noAnswer');
    case 'noFork':
      return t('slashCommands.builtin.fork.reason.unavailable');
    case 'noEffort':
      return t('slashCommands.builtin.effort.reason.noEffort');
    case 'agentOnly':
      return t('slashCommands.builtin.plan.reason.agentOnly');
    case 'fixed':
      return t('slashCommands.builtin.knowledge.reason.fixed');
    case 'noWorkspace':
      return t('slashCommands.reason.noWorkspace');
    case 'noAgent':
      return t('slashCommands.builtin.init.reason.noAgent');
    case 'notEditor':
      return t('slashCommands.builtin.init.reason.notEditor');
    case 'nothingToCompact':
      return t('slashCommands.builtin.compact.reason.nothing');
    case 'notOwner':
      return command.group === 'sandbox'
        ? t('slashCommands.reason.sandboxOwner')
        : t('slashCommands.builtin.compact.reason.owner');
    case 'noRunCode':
      return t('slashCommands.builtin.run.reason.noRunCode');
    case 'runCodeDenied':
      return t('slashCommands.builtin.run.reason.denied');
    case 'engineFixed':
      return t('slashCommands.builtin.engine.reason.fixed');
    case 'quickChat':
      return t('slashCommands.builtin.engine.reason.quickChat');
    case 'legacy':
      if (command.group === 'sandbox') return t('slashCommands.reason.sandboxLegacy');
      return command.name === 'usage'
        ? t('slashCommands.builtin.usage.reason.legacy')
        : command.name === 'compact'
          ? t('slashCommands.builtin.compact.reason.legacy')
          : t('slashCommands.builtin.permissions.reason.legacy');
    default:
      return command.name === 'mcp'
        ? t('slashCommands.builtin.mcp.reason.off')
        : command.name === 'memory'
          ? t('slashCommands.builtin.memory.reason.off')
          : t('slashCommands.builtin.skills.reason.off');
  }
}

/** The reason a highlighted entry cannot run, or null; Enter on it reads this out instead of running (§3). */
export function entryReasonText(t: TFunction, entry: MenuEntry | undefined): string | null {
  return entry?.kind === 'builtin' && entry.reason ? builtinReasonText(t, entry.command, entry.reason) : null;
}

interface SlashCommandMenuProps {
  open: boolean;
  entries: MenuEntry[];
  highlighted: number;
  onHighlight: (index: number) => void;
  onSelect: (entry: MenuEntry) => void;
  onDismiss: () => void;
  /** The composer's text box: a click there is not outside the menu. */
  inputRef: React.RefObject<HTMLElement | null>;
  /** What Enter on a greyed-out row said, read out politely. */
  announcement?: string | null;
  /** What a greyed row's reason names (`/repo`'s repository). */
  reasonValues?: BuiltinReasonValues;
}

/**
 * The `/` menu (SLASH1 D6, SLASH2 §3): the composer keeps focus and drives it
 * with the arrow keys, Enter/Tab and Escape (`aria-activedescendant`), as a
 * combobox's list; a pointer picks a row too. Rows are grouped (Built-in, Your
 * commands, Team, Organization); a built-in that does not apply here stays in
 * the list, greyed out with its reason, and does not run.
 */
export function SlashCommandMenu({
  open,
  entries,
  highlighted,
  onHighlight,
  onSelect,
  onDismiss,
  inputRef,
  announcement = null,
  reasonValues,
}: SlashCommandMenuProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement | null>(null);
  // The list is taller than the popover: the arrow keys must scroll the highlighted row into view, or Enter
  // would run a row the person cannot see.
  const { fromPointer } = useActiveOptionInView(
    listRef,
    '[aria-selected="true"]',
    open ? `${highlighted}/${entries.length}` : null,
  );
  const groups: Array<{ group: CommandGroup; rows: Array<{ entry: MenuEntry; index: number }> }> = [];
  entries.forEach((entry, index) => {
    const last = groups[groups.length - 1];
    if (last?.group === entry.group) last.rows.push({ entry, index });
    else groups.push({ group: entry.group, rows: [{ entry, index }] });
  });
  return (
    <Popover.Root open={open} onOpenChange={(next) => !next && onDismiss()}>
      <Popover.Anchor>
        <Box aria-hidden style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} />
      </Popover.Anchor>
      <Popover.Content
        side="top"
        align="start"
        sideOffset={8}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onInteractOutside={(e) => {
          if (inputRef.current?.contains(e.target as Node)) e.preventDefault();
        }}
        style={{
          padding: 'var(--space-1)',
          width: 'min(420px, calc(100vw - 32px))',
          maxHeight: 360,
          overflowY: 'auto',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-3)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        <Box ref={listRef} id={SLASH_MENU_ID} role="listbox" aria-label={t('slashCommands.menuLabel')} data-testid="slash-command-menu">
          {groups.map(({ group, rows }) => (
            <Box key={group} role="group" aria-labelledby={`slash-command-group-${group}`}>
              <Text
                as="div"
                id={`slash-command-group-${group}`}
                size="1"
                weight="bold"
                color="gray"
                style={{
                  padding: 'var(--space-2) var(--space-2) var(--space-1)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-1)',
                }}
              >
                {group === 'sandbox' ? <MaterialIcon name="terminal" size={12} color="var(--gray-11)" /> : null}
                {groupLabel(t, group)}
              </Text>
              {rows.map(({ entry, index }) => (
                <MenuRow
                  key={entry.id}
                  entry={entry}
                  reasonValues={reasonValues}
                  active={index === highlighted}
                  onHighlight={() => {
                    if (index === highlighted) return;
                    fromPointer();
                    onHighlight(index);
                  }}
                  onSelect={() => onSelect(entry)}
                />
              ))}
            </Box>
          ))}
        </Box>
        <Box aria-live="polite" data-testid="slash-command-announcement" style={{ padding: announcement ? 'var(--space-1) var(--space-2)' : 0 }}>
          {announcement ? (
            <Text size="1" color="gray">
              {announcement}
            </Text>
          ) : null}
        </Box>
      </Popover.Content>
    </Popover.Root>
  );
}

function MenuRow({
  entry,
  reasonValues,
  active,
  onHighlight,
  onSelect,
}: {
  entry: MenuEntry;
  reasonValues?: BuiltinReasonValues;
  active: boolean;
  onHighlight: () => void;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const disabled = entry.kind === 'builtin' && entry.reason !== null;
  const reasonId = `${slashOptionId(entry.id)}-reason`;
  let name: string;
  let description: string;
  let badge: React.ReactNode = null;
  let hint: string | null = null;
  let sharedBy: string | null = null;
  if (entry.kind === 'builtin') {
    name = `/${entry.command.name}`;
    description = builtinDescription(t, entry.command);
    const alias = entry.alias ?? entry.command.aliases[0];
    badge = alias ? (
      <Text size="1" color="gray" style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
        {t('slashCommands.alsoAlias', { alias: `/${alias}` })}
      </Text>
    ) : null;
    hint = entry.command.args || null;
  } else if (entry.kind === 'user') {
    name = `/${entry.command.name}`;
    description = entry.command.description;
    badge = <OwnerBadge owner={entry.command.owner} />;
    hint = argumentHint(entry.command.arguments) || null;
    sharedBy = sharedByText(t, entry.command);
  } else {
    name = t('slashCommands.moreBuiltins');
    description = t('slashCommands.moreCount', { n: entry.count });
  }
  return (
    <Flex
      id={slashOptionId(entry.id)}
      role="option"
      aria-selected={active}
      aria-disabled={disabled || undefined}
      aria-describedby={disabled ? reasonId : undefined}
      data-testid={`slash-command-option-${entry.id}`}
      direction="column"
      gap="1"
      onMouseDown={(e) => e.preventDefault()}
      onMouseEnter={onHighlight}
      onClick={onSelect}
      style={{
        padding: 'var(--space-2)',
        borderRadius: 'var(--radius-2)',
        cursor: disabled ? 'default' : 'pointer',
        backgroundColor: active ? 'var(--accent-a3)' : 'transparent',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text
          size="2"
          weight="medium"
          color={disabled ? 'gray' : undefined}
          style={{ fontFamily: entry.kind === 'more' ? undefined : 'monospace', flexShrink: 0 }}
        >
          {name}
        </Text>
        <Text size="1" color="gray" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {description}
        </Text>
        {badge}
      </Flex>
      {active && hint ? (
        <Text size="1" color="gray" style={{ fontFamily: 'monospace' }} data-testid="slash-command-hint">
          {hint}
        </Text>
      ) : null}
      {disabled && entry.kind === 'builtin' && entry.reason ? (
        <Flex align="center" gap="1" id={reasonId} data-testid="slash-command-reason">
          <MaterialIcon name="info" size={12} color="var(--gray-11)" />
          <Text size="1" color="gray">
            {builtinReasonText(t, entry.command, entry.reason, reasonValues)}
          </Text>
        </Flex>
      ) : null}
      {sharedBy ? (
        <Text size="1" color="gray" data-testid="slash-command-shared-by">
          {sharedBy}
        </Text>
      ) : null}
    </Flex>
  );
}

/**
 * Below the composer while it holds a built-in with its arguments (`/model gpt`), or a word PipesHub has no
 * command for (`/login`): the argument hint, why it does not apply here, or that the text goes as a message (O8).
 */
export function BuiltinCommandBar({
  state,
  announcement,
  reasonValues,
}: {
  state: Extract<ComposerCommandState, { phase: 'builtin' | 'word' }>;
  announcement: string | null;
  reasonValues?: BuiltinReasonValues;
}) {
  const { t } = useTranslation();
  if (state.phase === 'word') {
    return (
      <Box aria-live="polite" data-testid="slash-command-word" style={{ padding: '0 var(--space-1)' }}>
        <Text size="1" color="gray">
          {t('slashCommands.terminalWord', { name: state.word })}
        </Text>
      </Box>
    );
  }
  const { command, reason } = state;
  return (
    <Flex direction="column" gap="1" data-testid="slash-command-builtin" style={{ padding: '0 var(--space-1)' }}>
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="1" weight="medium" style={{ fontFamily: 'monospace' }}>
          /{command.name}
        </Text>
        {command.args ? (
          <Text size="1" color="gray" style={{ fontFamily: 'monospace' }}>
            {command.args}
          </Text>
        ) : null}
        <Text size="1" color="gray">
          {builtinDescription(t, command)}
        </Text>
      </Flex>
      <Box aria-live="polite">
        {reason || announcement ? (
          <Text size="1" color="gray" data-testid="slash-command-problem">
            {reason ? builtinReasonText(t, command, reason, reasonValues) : announcement}
          </Text>
        ) : null}
      </Box>
    </Flex>
  );
}

/** What keeps a command from being sent, in words; null when it renders. Literal keys, so `i18n:keys` checks them. */
export function commandProblemText(t: TFunction, result: RenderResult): string | null {
  if (!('code' in result)) return null;
  switch (result.code) {
    case 'command_argument_missing':
      return t('slashCommands.problem.missing', { names: result.names.join(', ') });
    case 'command_argument_invalid':
      return t('slashCommands.problem.invalid', { names: result.names.join(', ') });
    default:
      return t('slashCommands.problem.tooLong');
  }
}

/**
 * Below the composer while it holds a /command: the argument hint, what is
 * still missing (send waits for it) and a "Preview" of the exact text.
 */
export function CommandArgsBar({ state }: { state: Extract<ComposerCommandState, { phase: 'args' }> }) {
  const { t } = useTranslation();
  const [showPreview, setShowPreview] = useState(false);
  const { command, preview } = state;
  const hint = argumentHint(command.arguments);
  const problem = preview ? commandProblemText(t, preview) : null;
  return (
    <Flex direction="column" gap="1" data-testid="slash-command-args" style={{ padding: '0 var(--space-1)' }}>
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="1" weight="medium" style={{ fontFamily: 'monospace' }}>
          /{command.name}
        </Text>
        {hint ? (
          <Text size="1" color="gray" style={{ fontFamily: 'monospace' }}>
            {hint}
          </Text>
        ) : null}
        <OwnerBadge owner={command.owner} />
        {preview?.ok ? (
          <Button
            type="button"
            variant="ghost"
            size="1"
            color="gray"
            aria-expanded={showPreview}
            aria-controls="slash-command-preview"
            onClick={() => setShowPreview((v) => !v)}
            style={{ marginLeft: 'auto' }}
          >
            {t('slashCommands.preview')}
            <MaterialIcon name={showPreview ? 'expand_less' : 'expand_more'} size={14} />
          </Button>
        ) : null}
      </Flex>
      <Box aria-live="polite">
        {problem ? (
          <Text size="1" color="red" data-testid="slash-command-problem">
            {problem}
          </Text>
        ) : null}
      </Box>
      {showPreview && preview?.ok ? (
        <Box
          id="slash-command-preview"
          data-testid="slash-command-preview"
          style={{
            padding: 'var(--space-2)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            border: '1px solid var(--slate-a4)',
            maxHeight: 200,
            overflowY: 'auto',
          }}
        >
          <Text size="1" color="gray" as="p" style={{ marginBottom: 'var(--space-1)' }}>
            {t('slashCommands.previewNote')}
          </Text>
          <Text size="2" as="p" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {preview.text}
          </Text>
        </Box>
      ) : null}
    </Flex>
  );
}
