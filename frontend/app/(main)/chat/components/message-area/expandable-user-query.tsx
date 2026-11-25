'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Box, Flex, Heading, Text, Button, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { selectSkillsEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useSkillsStore } from '@/workspace/skills/personal/store';
import {
  CommandsApi,
  liveSentCommand,
  useCommandCatalog,
  type CommandProvenance,
  type SentCommand,
} from '@/chat/commands';
import { ownerLabel, sharedByText } from '@/chat/components/chat-panel/slash-command-menu';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { MentionIcon } from '@/chat/components/mention-icon';
import { locateMentionNames, mentionLabel, type MentionNode } from '@/chat/utils/node-mentions';
import { CHAT_LINK_STYLE, cutOutsideUrl, findUrls, shortLinkLabel } from '@/chat/utils/link-label';
import { parseQuotedSelection, readableQuotedSelection } from '@/chat/utils/quoted-selection';

export const QUESTION_CHAR_LIMIT = 250;
export const EXPANDED_MAX_HEIGHT_PX = 360;

export interface ExpandableUserQueryProps {
  question: string;
  isMobile?: boolean;
  messageId?: string;
  isStreaming?: boolean;
  onEdit?: () => void;
  /** The /command the question was sent as (SLASH1): drawn as its chip, with what was sent. */
  command?: SentCommand;
  /** Nodes the question was scoped to; where the text names one, it shows as a chip. */
  mentions?: readonly MentionNode[];
}

function MentionChip({ node }: { node: MentionNode }) {
  const text = mentionLabel(node);
  // The icon is glued to the first word, so a line never breaks between them.
  const firstBreak = text.search(/\s/);
  const head = firstBreak < 0 ? text : text.slice(0, firstBreak);
  const rest = firstBreak < 0 ? '' : text.slice(firstBreak);
  const chip = (
    <span
      style={{
        background: 'var(--accent-a3)',
        color: 'var(--accent-a11)',
        boxShadow: 'inset 0 0 0 1px var(--accent-a6)',
        borderRadius: 6,
        padding: '0 6px',
        margin: '0 2px',
        boxDecorationBreak: 'clone',
        WebkitBoxDecorationBreak: 'clone',
      }}
    >
      <span style={{ whiteSpace: 'nowrap' }}>
        <span style={{ display: 'inline-flex', verticalAlign: '-0.12em', marginRight: 5, lineHeight: 0 }} aria-hidden>
          <MentionIcon
            nodeType={node.nodeType}
            connector={node.connector}
            extension={node.extension}
            mimeType={node.mimeType}
            name={node.name}
            size={15}
          />
        </span>
        {head}
      </span>
      {rest}
    </span>
  );
  return text === node.name ? chip : <Tooltip content={node.name}>{chip}</Tooltip>;
}

function withLinks(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const { start, end, url } of findUrls(text)) {
    if (start > cursor) out.push(text.slice(cursor, start));
    out.push(
      <a
        key={`${keyPrefix}-${start}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={url}
        style={CHAT_LINK_STYLE}
      >
        {shortLinkLabel(url)}
      </a>
    );
    cursor = end;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

function withMentions(text: string, mentions: readonly MentionNode[]): React.ReactNode {
  const spans = locateMentionNames(text, mentions);
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) out.push(...withLinks(text.slice(cursor, span.start), `t${cursor}`));
    out.push(
      <MentionChip key={`${span.node.id}-${span.start}`} node={span.node} />
    );
    cursor = span.end;
  }
  if (cursor < text.length) out.push(...withLinks(text.slice(cursor), `t${cursor}`));
  return out;
}

function ActionIconButton({
  ariaLabel,
  icon,
  visible,
  onClick,
  color = 'var(--slate-11)',
}: {
  ariaLabel: string;
  icon: string;
  visible: boolean;
  onClick: () => void;
  color?: string;
}) {
  return (
    <IconButton
      variant="ghost"
      color="gray"
      size="1"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        cursor: 'pointer',
        flexShrink: 0,
        opacity: visible ? 1 : 0,
        transition: 'opacity 0.15s ease',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      <MaterialIcon name={icon} size={ICON_SIZES.PRIMARY} color={color} />
    </IconButton>
  );
}

function ToggleButton({
  expanded,
  onToggle,
}: {
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      color="gray"
      size="2"
      onClick={onToggle}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '2px',
        cursor: 'pointer',
        color: 'var(--slate-11)',
        background: 'none',
        padding: 0,
        fontFamily: 'inherit',
        height: 'auto',
      }}
    >
      {expanded ? 'Show less' : 'Show more'}
      <MaterialIcon
        name={expanded ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}
        size={ICON_SIZES.PRIMARY}
      />
    </Button>
  );
}

const SKILLS_PAGE = '/workspace/skills/personal';

/** "Save as command" (SLASH1 S3): the skills editor, opened on a new personal command holding this text. */
function SaveAsCommandButton({ text, visible }: { text: string; visible: boolean }) {
  const { t } = useTranslation();
  const label = t('slashCommands.saveAsCommand');
  return (
    <IconButton
      asChild
      variant="ghost"
      color="gray"
      size="1"
      style={{ flexShrink: 0, opacity: visible ? 1 : 0, transition: 'opacity 0.15s ease', pointerEvents: visible ? 'auto' : 'none' }}
    >
      <Link
        href={SKILLS_PAGE}
        aria-label={label}
        title={label}
        onClick={() => useSkillsStore.getState().openEditorPanel(null, { prefillBody: text, prefillCommand: true })}
      >
        <MaterialIcon name="bookmark_add" size={ICON_SIZES.PRIMARY} color="var(--slate-11)" />
      </Link>
    </IconButton>
  );
}

function QueryActions({
  question,
  showEdit,
  onEdit,
  visible,
  saveAsCommand = false,
}: {
  question: string;
  showEdit: boolean;
  onEdit?: () => void;
  visible: boolean;
  saveAsCommand?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(readableQuotedSelection(question)).then(() => {
      setCopied(true);
    }).catch(() => {});
  }, [question]);

  if (!question.trim() && !showEdit) return null;

  return (
    <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
      {question.trim() ? (
        <ActionIconButton
          ariaLabel={copied ? 'Copied' : 'Copy'}
          icon={copied ? 'check' : 'content_copy'}
          visible={visible}
          onClick={handleCopy}
          color={copied ? 'var(--accent-11)' : 'var(--slate-11)'}
        />
      ) : null}
      {showEdit && onEdit ? (
        <ActionIconButton
          ariaLabel="Edit"
          icon="edit"
          visible={visible}
          onClick={onEdit}
        />
      ) : null}
      {saveAsCommand && question.trim() ? <SaveAsCommandButton text={question} visible={visible} /> : null}
    </Flex>
  );
}

/** Literal keys per owner, so `i18n:keys` checks each one. */
function authorText(t: TFunction, command: SentCommand, names: CommandProvenance | null): string | null {
  if (command.owner === 'you') return t('slashCommands.byYou');
  if (command.owner === 'pipeshub') return t('slashCommands.byPipeshub');
  return names ? sharedByText(t, names) : null;
}

const isShared = (command: SentCommand): boolean => command.owner === 'team' || command.owner === 'org';

/** A question sent as a /command: its chip and typed arguments, and "What was sent" once the server recorded it. */
function CommandQuery({ command }: { command: SentCommand }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // A shared command's author and team are kept as ids and named when shown, so an erased author never is (S4).
  const [names, setNames] = useState<CommandProvenance | null>(null);
  useEffect(() => {
    if (!open || names || !isShared(command)) return;
    let live = true;
    const fallback: CommandProvenance = { owner: command.owner, ...(command.authorFormer ? { authorFormer: true } : {}) };
    if (!command.authorId && !command.teamId) {
      setNames(fallback);
      return;
    }
    CommandsApi.provenance(command)
      .then((read) => live && setNames(read))
      .catch(() => live && setNames({ owner: command.owner }));
    return () => {
      live = false;
    };
  }, [open, names, command]);
  const args = command.typed.replace(/^\/\S+\s*/, '');
  const author = authorText(t, command, names);
  const detailsId = `sent-command-${command.name}-${command.version}`;
  return (
    <Box style={{ paddingTop: 'var(--space-3)' }} data-testid="user-command-query">
      <Flex align="baseline" gap="2" wrap="wrap">
        <Text
          size="3"
          weight="medium"
          data-testid="user-command-chip"
          style={{
            fontFamily: 'monospace',
            padding: '2px var(--space-2)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--accent-a3)',
            color: 'var(--accent-11)',
            flexShrink: 0,
          }}
        >
          /{command.name}
        </Text>
        {args ? (
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)', lineHeight: 1.3, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {args}
          </Heading>
        ) : null}
      </Flex>
      {command.expanded === undefined && command.files !== undefined ? (
        <Text size="1" color="gray" as="p" style={{ marginTop: 'var(--space-2)' }} data-testid="user-command-review-files">
          {t('slashCommands.reviewOfFiles', { count: command.files })}
        </Text>
      ) : null}
      {command.expanded !== undefined ? (
        <Box style={{ marginTop: 'var(--space-2)' }}>
          <Button
            type="button"
            variant="ghost"
            color="gray"
            size="1"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((v) => !v)}
            data-testid="user-command-what-was-sent"
          >
            {t('slashCommands.whatWasSent')}
            <MaterialIcon name={open ? 'keyboard_arrow_up' : 'keyboard_arrow_down'} size={ICON_SIZES.PRIMARY} />
          </Button>
          {open ? (
            <Box
              id={detailsId}
              data-testid="user-command-sent-text"
              style={{
                marginTop: 'var(--space-2)',
                backgroundColor: 'var(--slate-3)',
                border: '1px solid var(--slate-a4)',
                borderRadius: 'var(--radius-3)',
                padding: 'var(--space-3)',
              }}
            >
              <Text size="2" as="p" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: EXPANDED_MAX_HEIGHT_PX, overflowY: 'auto' }}>
                {command.expanded}
              </Text>
              <Text size="1" color="gray" as="p" style={{ marginTop: 'var(--space-2)' }} data-testid="user-command-provenance">
                {[`${command.name} v${command.version}`, author, names?.team ? null : ownerLabel(t, command.owner)]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            </Box>
          ) : null}
        </Box>
      ) : null}
    </Box>
  );
}

export function ExpandableUserQuery({
  question,
  isMobile = false,
  messageId,
  isStreaming = false,
  onEdit,
  command,
  mentions = [],
}: ExpandableUserQueryProps) {
  const [expanded, setExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);
  const catalog = useCommandCatalog((s) => s.commands);
  const skillsEnabled = useFeatureFlagsStore(selectSkillsEnabled);
  const canHover = useCanHover();

  useEffect(() => setExpanded(false), [question]);

  // While the turn streams the server's record is not back yet: the chip comes from the menu's list.
  const sentCommand = command ?? (isStreaming ? liveSentCommand(question, catalog) : null);
  if (sentCommand) return <CommandQuery command={sentCommand} />;

  const quoted = parseQuotedSelection(question);
  const displayQuestion = quoted.question;
  const isLong = Boolean(displayQuestion.trim()) && displayQuestion.length > QUESTION_CHAR_LIMIT;
  const showEdit = Boolean(!isStreaming && messageId && onEdit);
  const toggle = () => setExpanded((prev) => !prev);

  const quoteCard = quoted.excerpt ? (
    <Box
      data-testid="user-query-quote"
      style={{
        marginTop: 'var(--space-3)',
        marginBottom: displayQuestion.trim() ? 'var(--space-2)' : 0,
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--olive-3)',
        borderLeft: '3px solid var(--olive-8)',
        color: 'var(--slate-11)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontSize: 'var(--font-size-2)',
        lineHeight: 1.45,
      }}
    >
      {quoted.excerpt}
    </Box>
  ) : null;

  if (!isLong || !expanded) {
    const displayed =
      isLong && !expanded
        ? displayQuestion.slice(0, cutOutsideUrl(displayQuestion, QUESTION_CHAR_LIMIT)).trimEnd() + '…'
        : displayQuestion;

    return (
      <Box
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {quoteCard}
        <Heading
          size="5"
          weight="medium"
          data-testid="user-query-heading"
          style={{
            color: 'var(--slate-12)',
            lineHeight: 1.3,
            paddingTop: quoted.excerpt ? 0 : 'var(--space-3)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {displayed ? withMentions(displayed, mentions) : null}
          <span
            data-testid="user-query-inline-actions"
            style={{ marginLeft: 'var(--space-2)', verticalAlign: 'middle', display: 'inline-flex' }}
          >
            <QueryActions
              question={question}
              showEdit={showEdit}
              onEdit={onEdit}
              visible={hovered || isMobile || !canHover}
              saveAsCommand={skillsEnabled && !isStreaming}
            />
          </span>
        </Heading>
        {isLong ? (
          <Box style={{ marginTop: 'var(--space-2)' }}>
            <ToggleButton expanded={false} onToggle={toggle} />
          </Box>
        ) : null}
      </Box>
    );
  }

  return (
    <Box style={{ paddingTop: quoted.excerpt ? 0 : 'var(--space-3)' }}>
      {quoteCard}
      <Box
        data-testid="user-query-bubble"
        style={{
          backgroundColor: 'var(--slate-3)',
          border: '1px solid var(--slate-a4)',
          borderRadius: 'var(--radius-3)',
          overflow: 'hidden',
        }}
      >
        <Text
          data-testid="user-query-text"
          size="2"
          style={{
            display: 'block',
            color: 'var(--slate-12)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            lineHeight: 1.55,
            padding: 'var(--space-3)',
            maxHeight: `${EXPANDED_MAX_HEIGHT_PX}px`,
            overflowY: 'auto',
          }}
        >
          {withMentions(displayQuestion, mentions)}
        </Text>
        <Flex
          data-testid="user-query-actions"
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-2) var(--space-3)',
            borderTop: '1px solid var(--slate-a4)',
            backgroundColor: 'var(--slate-2)',
          }}
        >
          <ToggleButton expanded onToggle={toggle} />
          <QueryActions
            question={question}
            showEdit={showEdit}
            onEdit={onEdit}
            visible
          />
        </Flex>
      </Box>
    </Box>
  );
}
