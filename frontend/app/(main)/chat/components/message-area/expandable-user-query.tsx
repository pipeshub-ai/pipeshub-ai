'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Box, Button, Flex, Heading, Text, IconButton, VisuallyHidden } from '@radix-ui/themes';
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
import { locateMentionNames, mentionLabel, withheldSourceSpans, withWithheldSpans, type MentionNode } from '@/chat/utils/node-mentions';
import { CHAT_LINK_STYLE, cutOutsideUrl, findUrls, shortLinkLabel } from '@/chat/utils/link-label';
import { parseQuotedSelection, readableQuotedSelection } from '@/chat/utils/quoted-selection';
import { chatExtension, chatSlots } from '@/config';
import { questionDisplay } from './question-display';
import { InlinePasteChip } from './sent-paste-chip';
import { SourceChip, WithheldSourceChip } from '../source-chip';

export interface ExpandableUserQueryProps {
  question: string;
  isMobile?: boolean;
  messageId?: string;
  isStreaming?: boolean;
  onEdit?: () => void;
  /** The /command the question was sent as (SLASH1): drawn as its chip, with what was sent. */
  command?: SentCommand;
  /**
   * Sources the question named with `@`: each is drawn as its chip in the heading, as the composer drew it. The
   * applied-filter pill under the question stays: it is the filter, the chip the mention.
   */
  mentions?: readonly MentionNode[];
  /** Where in `question` the server wrote the word for a source the reader can't open (the row's `withheldSources`). */
  withheldSources?: readonly number[];
  /** Under a byline: 20 px, 18 px when `compact`. Without it the window width decides. */
  headline?: { compact: boolean };
}

const NO_MENTIONS: readonly MentionNode[] = [];

/** Clips a long question for its heading without cutting a URL in half (the URL is kept whole), then as the extension clips. */
const clipHeading = (text: string, limit: number) => chatExtension.question.truncate(text, cutOutsideUrl(text, limit));

/** The question's tracking at each of its two sizes. */
const HEADLINE_TRACKING = { '4': '-0.04px', '5': '-0.08px' } as const;

/**
 * Points Radix's per-size tracking variables at the question's own values, so the tracking follows whichever
 * size the responsive `size` prop resolves to at the current width.
 */
const HEADLINE_TRACKING_VARS = Object.fromEntries(
  Object.entries(HEADLINE_TRACKING).map(([size, value]) => [`--letter-spacing-${size}`, value])
) as React.CSSProperties;

/** The heading with each named source as its chip; the text between goes through `withLinks`. */
function withSources(
  text: string,
  mentions: readonly MentionNode[],
  withheld: ReadonlyArray<{ start: number; end: number }>,
): React.ReactNode[] {
  const spans = withWithheldSpans(mentions.length > 0 ? locateMentionNames(text, mentions) : [], withheld);
  if (spans.length === 0) return withLinks(text, 't0');
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) out.push(...withLinks(text.slice(cursor, span.start), `t${cursor}`));
    out.push(
      span.node ? (
        <SourceChip key={`s${span.start}`} source={span.node} label={mentionLabel(span.node)} />
      ) : (
        <WithheldSourceChip key={`w${span.start}`} />
      )
    );
    cursor = span.end;
  }
  if (cursor < text.length) out.push(...withLinks(text.slice(cursor), `t${cursor}`));
  return out;
}

// URLs become short links; the text around them is drawn by the chat extension's `QueryText`.
function withLinks(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const { start, end, url } of findUrls(text)) {
    if (start > cursor) {
      out.push(<chatSlots.QueryText key={`${keyPrefix}-${cursor}`} text={text.slice(cursor, start)} />);
    }
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
  if (cursor < text.length) {
    out.push(<chatSlots.QueryText key={`${keyPrefix}-${cursor}`} text={text.slice(cursor)} />);
  }
  return out;
}

function ActionIconButton({
  ariaLabel,
  icon,
  visible,
  onClick,
  color = 'var(--text-secondary)',
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
      className="touch-hit"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        cursor: 'pointer',
        flexShrink: 0,
        opacity: visible ? 1 : 0,
        transition: 'opacity var(--motion-micro) var(--motion-ease)',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      <MaterialIcon name={icon} size={ICON_SIZES.PRIMARY} color={color} />
    </IconButton>
  );
}

const SKILLS_PAGE = '/workspace/skills/personal';

/** How tall "What was sent" grows before it scrolls. */
const SENT_TEXT_MAX_HEIGHT_PX = 360;

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

export function QueryActions({
  question,
  showEdit,
  onEdit,
  visible,
  saveAsCommand = false,
  reserveEdit = false,
}: {
  question: string;
  showEdit: boolean;
  onEdit?: () => void;
  visible: boolean;
  saveAsCommand?: boolean;
  /**
   * Edit is offered once the answer is done: while it runs its place is kept, unseen and out of reach, so the
   * question's last line does not re-wrap (and push the answer down) the moment Edit appears.
   */
  reserveEdit?: boolean;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  // The two 28 px buttons sit apart with a pointer, and on touch, where each tap area grows to 44 px,
  // spread until those areas meet; neither pair overlaps.
  const canHover = useCanHover();

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(readableQuotedSelection(chatExtension.question.toPlainText(question))).then(() => {
      setCopied(true);
    }).catch(() => {});
  }, [question]);

  const holdEditPlace = reserveEdit && !(showEdit && onEdit);
  if (!question.trim() && !showEdit && !holdEditPlace) return null;

  return (
    <Flex align="center" gap={canHover ? '2' : '5'} style={{ flexShrink: 0 }}>
      {question.trim() ? (
        <ActionIconButton
          ariaLabel={copied ? t('chat.question.copied') : t('chat.copy')}
          icon={copied ? 'check' : 'content_copy'}
          visible={visible}
          onClick={handleCopy}
          // Inside a gray Radix control, so the brand green is named directly.
          color={copied ? 'var(--emerald-11)' : 'var(--text-secondary)'}
        />
      ) : null}
      {showEdit && onEdit ? (
        <ActionIconButton
          ariaLabel={t('chat.question.edit')}
          icon="edit"
          visible={visible}
          onClick={onEdit}
        />
      ) : holdEditPlace ? (
        <span
          data-testid="user-query-edit-placeholder"
          aria-hidden
          inert
          style={{ display: 'inline-flex', flexShrink: 0, visibility: 'hidden' }}
        >
          <ActionIconButton ariaLabel={t('chat.question.edit')} icon="edit" visible={false} onClick={() => {}} />
        </span>
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
              <Text size="2" as="p" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: SENT_TEXT_MAX_HEIGHT_PX, overflowY: 'auto' }}>
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

/**
 * The question at the top of a turn: a 20 px `<h1>` (18 px on a narrow screen) whatever its length, with Copy
 * and Edit beside it (outside the heading, so its name is the question alone).
 * Long or fenced material is lifted out of the heading into "pasted" chips
 * under it; what was sent is unchanged.
 */
export function ExpandableUserQuery({
  question,
  isMobile = false,
  messageId,
  isStreaming = false,
  onEdit,
  command,
  headline,
  mentions = NO_MENTIONS,
  withheldSources,
}: ExpandableUserQueryProps) {
  const [hovered, setHovered] = useState(false);
  const catalog = useCommandCatalog((s) => s.commands);
  const skillsEnabled = useFeatureFlagsStore(selectSkillsEnabled);
  const [focusWithin, setFocusWithin] = useState(false);
  const canHover = useCanHover();
  // A question sent in this session settles in; one loaded with the chat is simply there.
  const [arrivesNow] = useState(isStreaming);

  // A follow-up that quotes an answer is stored with the excerpt first: the excerpt shows as a quote card above it.
  const quoted = parseQuotedSelection(question);
  const display = questionDisplay(chatSlots.useQuestionHeading(quoted.question), clipHeading);

  // While the turn streams the server's record is not back yet: the chip comes from the menu's list.
  const sentCommand = command ?? (isStreaming ? liveSentCommand(question, catalog) : null);
  if (sentCommand) return <CommandQuery command={sentCommand} />;
  // The question is the turn's anchor, so it keeps one size: a long one folds into a chip instead of shrinking.
  const size = headline ? (headline.compact ? '4' : '5') : ({ initial: '4', md: '5' } as const);
  const showEdit = Boolean(!isStreaming && messageId && onEdit);
  const actionsVisible = hovered || focusWithin || isMobile || !canHover;

  const actions = (
    <span data-testid="user-query-inline-actions" style={{ display: 'inline-flex', verticalAlign: 'middle' }}>
      <QueryActions
        question={question}
        showEdit={showEdit}
        onEdit={onEdit}
        visible={actionsVisible}
        reserveEdit={Boolean(isStreaming && onEdit)}
        saveAsCommand={skillsEnabled && !isStreaming}
      />
    </span>
  );

  const quoteCard = quoted.excerpt ? (
    <Box
      data-testid="user-query-quote"
      style={{
        marginBottom: quoted.question.trim() ? 'var(--space-2)' : 0,
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

  const heading = (
    <Text size={size} weight="medium" asChild>
      <h1
        data-testid="user-query-heading"
        style={{
          display: 'inline',
          margin: 0,
          ...HEADLINE_TRACKING_VARS,
          color: 'var(--text-primary)',
        }}
      >
        {withSources(display.heading, mentions, withheldSourceSpans(display.heading, question, withheldSources))}
      </h1>
    </Text>
  );

  return (
    <Box
      className={arrivesNow ? 'conversation-fade-in' : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocusWithin(true)}
      onBlur={(event: React.FocusEvent<HTMLElement>) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusWithin(false);
      }}
    >
      {quoteCard}
      {display.headingHidden ? (
        <VisuallyHidden asChild>
          <h1 data-testid="user-query-heading">{display.heading}</h1>
        </VisuallyHidden>
      ) : (
        // The line box around the heading carries its size, so the actions sit on its last line.
        <Text size={size} weight="medium" asChild>
          <div
            style={{
              margin: 0,
              ...HEADLINE_TRACKING_VARS,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {heading}
            <span aria-hidden style={{ display: 'inline-block', width: 'var(--space-2)' }} />
            {actions}
          </div>
        </Text>
      )}
      {display.pastes.length > 0 && (
        <Flex
          data-testid="user-query-pastes"
          align="center"
          gap="2"
          wrap="wrap"
          style={{ marginTop: display.headingHidden ? 0 : 'var(--space-3)' }}
        >
          {display.pastes.map((paste, index) => (
            <InlinePasteChip key={`${paste.kind}-${index}`} kind={paste.kind} text={paste.text} />
          ))}
          {display.headingHidden && actions}
        </Flex>
      )}
    </Box>
  );
}
