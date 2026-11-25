'use client';

import React from 'react';
import { Box, Flex, IconButton, Popover, Separator, Switch, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { DEFAULT_AGENT_CAPABILITIES } from '@/chat/types';
import type { HarnessSwitchLock } from '@/chat/assistant-harness';
import type { TurnGateKey, TurnGatesChoice } from '@/chat/run-gates';

export function hasNonDefaultSearchCapabilities(
  internalSearch: boolean,
  webSearch: boolean,
): boolean {
  return (
    internalSearch !== DEFAULT_AGENT_CAPABILITIES.internalSearch ||
    webSearch !== DEFAULT_AGENT_CAPABILITIES.webSearch
  );
}

export function PlusMenuTriggerIcon({
  color,
  showFilterBadge,
}: {
  color: string;
  showFilterBadge: boolean;
}) {
  return (
    <Box style={{ position: 'relative', display: 'inline-flex' }}>
      <MaterialIcon name="add" size={16} color={color} />
      {showFilterBadge && (
        <Box
          data-testid="plus-menu-filter-badge"
          aria-hidden
          style={{
            position: 'absolute',
            top: -1,
            right: -1,
            width: 7,
            height: 7,
            borderRadius: '50%',
            backgroundColor: 'var(--red-9)',
            boxShadow: '0 0 0 1.5px var(--color-panel-solid)',
            pointerEvents: 'none',
          }}
        />
      )}
    </Box>
  );
}

export interface PlusMenuCapabilities {
  internalSearch: boolean;
  webSearch: boolean;
  onToggleInternalSearch: (enabled: boolean) => void;
  onToggleWebSearch: (enabled: boolean) => void;
  /**
   * `false` = the agent wasn't configured with this capability, so the toggle
   * is disabled. `undefined` = universal mode, capability always available.
   */
  agentHasInternalSearch?: boolean;
  agentHasWebSearch?: boolean;
  /**
   * Web search is shown off only because no admin has set it up yet. That is not a filter the
   * person chose, so it does not mark the + button as filtered.
   */
  webSearchNotSetUp?: boolean;
  /** The "New agent harness" switch (CTL-7); absent where the org's agent harness is off. */
  harness?: HarnessSwitch;
  /** "Work on a repository" (WP-26); absent where the chat is not on the agent harness or not the viewer's. */
  repository?: RepositoryMenuEntry;
  /** This message's run gates (G-03..G-05); absent where the chat is not on the agent harness or not the viewer's. */
  gates?: GatesMenuEntry;
}

export interface GatesMenuEntry {
  turn: TurnGatesChoice;
  /** What the agent holds every run to: shown on, and cannot be turned off here. */
  locks: Pick<TurnGatesChoice, 'planApproval' | 'verify'>;
  onToggle: (key: TurnGateKey, on: boolean) => void;
}

const GATE_ROWS: ReadonlyArray<{ key: TurnGateKey; title: string; subtitle: string; locked?: string }> = [
  { key: 'dryRun', title: 'chat.runGates.dryRun', subtitle: 'chat.runGates.dryRunHint' },
  {
    key: 'planApproval',
    title: 'chat.runGates.planApproval',
    subtitle: 'chat.runGates.planApprovalHint',
    locked: 'chat.runGates.planApprovalAgent',
  },
  { key: 'verify', title: 'chat.runGates.verify', subtitle: 'chat.runGates.verifyHint', locked: 'chat.runGates.verifyAgent' },
];

/** "Dry run", "Approve plan first", "Check the result": each with what it does, in words (WP-31 A). */
function GatesRows({ turn, locks, onToggle }: GatesMenuEntry) {
  const { t } = useTranslation();
  return (
    <>
      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />
      {GATE_ROWS.map((row) => {
        const locked = row.key !== 'dryRun' && locks[row.key];
        const on = locked || turn[row.key];
        const title = t(row.title);
        return (
          <PlusMenuRow key={row.key} tall disabled={locked} onClick={() => onToggle(row.key, !on)} testId={`plus-menu-gate-${row.key}`}>
            <Flex direction="column" style={{ minWidth: 0, padding: 'var(--space-1) 0' }}>
              <Text size="2" style={{ color: 'var(--slate-12)' }}>
                {title}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
                {locked && row.locked ? t(row.locked) : t(row.subtitle)}
              </Text>
            </Flex>
            <Switch
              size="1"
              aria-label={title}
              checked={on}
              disabled={locked}
              onCheckedChange={(checked) => onToggle(row.key, checked)}
              onClick={(e) => e.stopPropagation()}
            />
          </PlusMenuRow>
        );
      })}
    </>
  );
}

export interface RepositoryMenuEntry {
  /** Why it cannot be used here, in words; null when it can. */
  reason: string | null;
  onSelect: () => void;
}

function RepositoryRow({ reason, onSelect }: RepositoryMenuEntry) {
  const { t } = useTranslation();
  const disabled = reason !== null;
  return (
    <PlusMenuRow onClick={onSelect} disabled={disabled} tall testId="plus-menu-repository">
      <Flex align="start" gap="2" style={{ minWidth: 0, padding: 'var(--space-1) 0' }}>
        <MaterialIcon name="call_split" size={16} color="var(--neutral-11)" />
        <Flex direction="column" style={{ minWidth: 0 }}>
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('chat.repository.menuTitle')}
          </Text>
          <Text size="1" data-testid="plus-menu-repository-subtitle" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
            {reason ?? t('chat.repository.menuSubtitle')}
          </Text>
        </Flex>
      </Flex>
    </PlusMenuRow>
  );
}

export interface HarnessSwitch {
  checked: boolean;
  locked?: HarnessSwitchLock;
  onCheckedChange: (checked: boolean) => void;
}

function HarnessSwitchRow({ checked, locked, onCheckedChange }: HarnessSwitch) {
  const { t } = useTranslation();
  return (
    <>
      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />
      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.newHarness', { defaultValue: 'New agent harness' })}
        checked={checked}
        keepChecked
        badge={<BetaBadge tooltip={null} />}
        disabled={locked !== undefined}
        tooltip={
          locked === 'agent'
            ? t('chat.agentCapabilities.newHarnessAgentNeeds', {
                defaultValue: 'This agent needs the new agent harness',
              })
            : locked === 'paused'
            ? t('chat.agentCapabilities.newHarnessPaused', {
                defaultValue: 'New agent harness runs are paused for your organization. Chats use the classic chat until they resume.',
              })
            : t('chat.agentCapabilities.newHarnessTooltip', {
                defaultValue: 'Durable runs, live timeline and approvals. Turn off to use the classic chat.',
              })
        }
        onCheckedChange={onCheckedChange}
      />
      {locked === 'conversation' ? (
        <Text size="1" data-testid="harness-switch-hint" style={{ color: 'var(--slate-11)', padding: '0 var(--space-2) var(--space-1)' }}>
          {t('chat.agentCapabilities.newHarnessNewChats', { defaultValue: 'Applies to new chats' })}
        </Text>
      ) : locked === 'paused' ? (
        <Text size="1" role="note" data-testid="harness-switch-paused" style={{ color: 'var(--amber-11)', padding: '0 var(--space-2) var(--space-1)' }}>
          {t('chat.agentCapabilities.newHarnessPausedHint', { defaultValue: 'Paused for now: chats use the classic chat' })}
        </Text>
      ) : null}
    </>
  );
}

interface PlusMenuContentProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
}

function PlusMenuRow({
  children,
  onClick,
  disabled,
  tall,
  testId,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  /** Two lines (a title and why): at least a touch target's height instead of the toggles' 32px. */
  tall?: boolean;
  testId?: string;
}) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      role={onClick ? 'menuitem' : undefined}
      tabIndex={onClick && !disabled ? 0 : undefined}
      aria-disabled={disabled || undefined}
      data-testid={testId}
      onClick={disabled ? undefined : onClick}
      onKeyDown={
        disabled || !onClick
          ? undefined
          : (e: React.KeyboardEvent) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick();
              }
            }
      }
      style={{
        ...(tall ? { minHeight: 44 } : { height: 32 }),
        padding: '0 var(--space-2)',
        borderRadius: 'var(--radius-1)',
        backgroundColor: hovered && !disabled ? 'var(--olive-3)' : undefined,
        outline: 'none',
        cursor: disabled ? 'not-allowed' : onClick ? 'pointer' : 'default',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {children}
    </Flex>
  );
}

function PlusMenuToggleRow({
  label,
  checked,
  keepChecked,
  disabled,
  disabledTooltip,
  tooltip,
  badge,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  /** A disabled row still shows `checked` (a fixed state), not off (a capability the agent lacks). */
  keepChecked?: boolean;
  disabled?: boolean;
  disabledTooltip?: string;
  tooltip?: string;
  /** Shown beside the label, e.g. a Beta badge. */
  badge?: React.ReactNode;
  onCheckedChange: (checked: boolean) => void;
}) {
  const row = (
    <PlusMenuRow
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text size="2" style={{ color: 'var(--slate-12)' }}>
          {label}
        </Text>
        {badge}
      </Flex>
      <Switch
        size="1"
        aria-label={label}
        checked={checked && (keepChecked || !disabled)}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        onClick={(e) => e.stopPropagation()}
      />
    </PlusMenuRow>
  );
  const content = (disabled && disabledTooltip) || tooltip;
  return content ? <Tooltip content={content}>{row}</Tooltip> : row;
}

/** Shared menu body — attach files + capability toggles — used by both the desktop popover and mobile sheet. */
export function PlusMenuContent({
  onAttachFiles,
  internalSearch,
  webSearch,
  onToggleInternalSearch,
  onToggleWebSearch,
  agentHasInternalSearch,
  agentHasWebSearch,
  harness,
  repository,
  gates,
}: PlusMenuContentProps) {
  const { t } = useTranslation();
  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <PlusMenuRow onClick={onAttachFiles}>
        <Flex align="center" gap="2">
          <MaterialIcon name="attach_file" size={16} color="var(--neutral-11)" />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('chat.attachmentTooltip', { defaultValue: 'Attach files' })}
          </Text>
        </Flex>
      </PlusMenuRow>
      {repository ? <RepositoryRow {...repository} /> : null}

      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />

      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.internalSearch', { defaultValue: 'Internal search' })}
        checked={internalSearch}
        disabled={agentHasInternalSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleInternalSearch}
      />
      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.webSearch', { defaultValue: 'Web search' })}
        checked={webSearch}
        disabled={agentHasWebSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleWebSearch}
      />
      {gates ? <GatesRows {...gates} /> : null}
      {harness ? <HarnessSwitchRow {...harness} /> : null}
    </Flex>
  );
}

/** Choosing the row closes the menu first, so the picker opens over the composer, not under the menu. */
function closingOnSelect(entry: RepositoryMenuEntry | undefined, close: () => void): RepositoryMenuEntry | undefined {
  return entry
    ? {
        ...entry,
        onSelect: () => {
          close();
          entry.onSelect();
        },
      }
    : undefined;
}

export interface PlusMenuButtonProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
  activeIconColor: string;
  disabled?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * A composer popover opens above the box; with the Labs rows it is taller than the room there on a short window, and
 * Radix flips to the larger side but never shrinks it. Held to the room Radix measured, it scrolls instead of running
 * off the screen, so its first row ("Attach files") stays reachable (WP-71).
 */
export const COMPOSER_POPOVER_FITS_VIEWPORT = {
  maxHeight: 'var(--radix-popover-content-available-height)',
  overflowY: 'auto',
  overscrollBehavior: 'contain',
} as const;

/**
 * Desktop "+" trigger that replaces the old Agent / Internal Search / Web
 * Search mode pill. Opens a popover with "Attach files" plus the Internal
 * Search / Web Search capability toggles.
 */
export function PlusMenuButton({
  onAttachFiles,
  activeIconColor,
  disabled,
  open,
  onOpenChange,
  ...capabilities
}: PlusMenuButtonProps) {
  const { t } = useTranslation();
  const showFilterBadge = hasNonDefaultSearchCapabilities(
    capabilities.internalSearch,
    capabilities.webSearch || Boolean(capabilities.webSearchNotSetUp),
  );
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger>
        <IconButton
          variant="ghost"
          color="gray"
          size="2"
          className="touch-size"
          disabled={disabled}
          aria-label={
            showFilterBadge
              ? t('chat.plusMenu.ariaLabelFiltersApplied', {
                  defaultValue: 'Attach files and capabilities. Search filters applied',
                })
              : t('chat.plusMenu.ariaLabel', { defaultValue: 'Attach files and capabilities' })
          }
          style={{
            margin: 0,
            width: 32,
            height: 32,
            minWidth: 32,
            padding: 0,
            boxSizing: 'border-box',
            backgroundColor: open ? 'var(--olive-3)' : undefined,
            borderRadius: 'var(--radius-2)',
            cursor: disabled ? 'default' : 'pointer',
          }}
        >
          <PlusMenuTriggerIcon
            color={disabled ? 'var(--slate-5)' : activeIconColor}
            showFilterBadge={showFilterBadge}
          />
        </IconButton>
      </Popover.Trigger>
      <Popover.Content
        data-composer-popover=""
        side="top"
        align="start"
        // Opening from the mouse should not paint a focus ring on the first row.
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          ...COMPOSER_POPOVER_FITS_VIEWPORT,
          width: capabilities.repository || capabilities.gates ? 280 : 216,
          maxWidth: 'calc(100vw - 32px)',
          padding: 6,
          // Same surface as the settings row menus: frosted, one olive-3 border, no ring shadow
          backgroundColor: 'var(--frost-bg-strong)',
          backdropFilter: 'var(--frost-filter)',
          WebkitBackdropFilter: 'var(--frost-filter)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          boxShadow: 'none',
        }}
      >
        <PlusMenuContent
          onAttachFiles={() => {
            onOpenChange(false);
            onAttachFiles();
          }}
          {...capabilities}
          repository={closingOnSelect(capabilities.repository, () => onOpenChange(false))}
        />
      </Popover.Content>
    </Popover.Root>
  );
}

export interface PlusMenuSheetProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Mobile bottom-sheet variant of the "+" menu. */
export function PlusMenuSheet({
  onAttachFiles,
  open,
  onOpenChange,
  ...capabilities
}: PlusMenuSheetProps) {
  const { t } = useTranslation();
  return (
    <MobileBottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={t('chat.plusMenu.title', { defaultValue: 'Add & capabilities' })}
    >
      <PlusMenuContent
        onAttachFiles={() => {
          onOpenChange(false);
          onAttachFiles();
        }}
        {...capabilities}
        repository={closingOnSelect(capabilities.repository, () => onOpenChange(false))}
      />
    </MobileBottomSheet>
  );
}
