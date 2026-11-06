'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Flex, Text, IconButton, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { locateMentionNames, mentionLabel, type MentionNode } from '@/chat/utils/node-mentions';
import { CHAT_LINK_STYLE, findUrls, shortLinkLabel } from '@/chat/utils/link-label';
import { chatExtension, chatSlots } from '@/config';
import { questionDisplay } from './question-display';
import { InlinePasteChip } from './sent-paste-chip';
import { SourceChip } from '../source-chip';

export interface ExpandableUserQueryProps {
  question: string;
  isMobile?: boolean;
  messageId?: string;
  isStreaming?: boolean;
  onEdit?: () => void;
  /**
   * Sources the question named with `@`: each is drawn as its chip in the heading, as the composer drew it. The
   * applied-filter pill under the question stays: it is the filter, the chip the mention.
   */
  mentions?: readonly MentionNode[];
  /** Under a byline: 20 px, 18 px when `compact`. Without it the window width decides. */
  headline?: { compact: boolean };
}

const NO_MENTIONS: readonly MentionNode[] = [];

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
function withSources(text: string, mentions: readonly MentionNode[]): React.ReactNode[] {
  const spans = mentions.length > 0 ? locateMentionNames(text, mentions) : [];
  if (spans.length === 0) return withLinks(text, 't0');
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) out.push(...withLinks(text.slice(cursor, span.start), `t${cursor}`));
    out.push(<SourceChip key={`s${span.start}`} source={span.node} label={mentionLabel(span.node)} />);
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

export function QueryActions({
  question,
  showEdit,
  onEdit,
  visible,
  reserveEdit = false,
}: {
  question: string;
  showEdit: boolean;
  onEdit?: () => void;
  visible: boolean;
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
    void navigator.clipboard.writeText(chatExtension.question.toPlainText(question)).then(() => {
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
    </Flex>
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
  headline,
  mentions = NO_MENTIONS,
}: ExpandableUserQueryProps) {
  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const canHover = useCanHover();
  // A question sent in this session settles in; one loaded with the chat is simply there.
  const [arrivesNow] = useState(isStreaming);

  const display = questionDisplay(chatSlots.useQuestionHeading(question), chatExtension.question.truncate);
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
      />
    </span>
  );

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
        {withSources(display.heading, mentions)}
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
