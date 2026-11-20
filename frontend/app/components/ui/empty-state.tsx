'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { EmptyIcon } from './empty-icon';

/**
 * The one empty state for a list, table or panel with nothing in it.
 *
 * - `panel` (default): a quiet, text-only message centred in the space the
 *   content would fill. Pass `icon` only where the area is tall enough to
 *   look blank without one.
 * - `no-results`: a search or filter matched nothing. Taller, with the empty
 *   glyph by default. Use `NoResults` for it.
 *
 * This primitive holds no copy: callers pass translated strings, so each
 * surface can say what is true for it.
 */

export type EmptyStateVariant = 'panel' | 'no-results';

interface PanelEmptyStateProps {
  variant?: 'panel';
  title: string;
  description?: React.ReactNode;
  /** Glyph above the title. None unless passed. */
  icon?: React.ReactNode | null;
  /** A button or link under the text. */
  action?: React.ReactNode;
  /** Draws the title as this heading, where it heads the page's content. */
  titleAs?: 'h2' | 'h3';
  style?: React.CSSProperties;
}

interface NoResultsEmptyStateProps {
  variant: 'no-results';
  title: string;
  /** What to try next, such as changing the search or clearing the filters. */
  description: React.ReactNode;
  /** Replaces the empty glyph; null shows none. */
  icon?: React.ReactNode | null;
  /** No action: the way forward is the search or filter itself. */
  action?: never;
  style?: React.CSSProperties;
}

export type EmptyStateProps = PanelEmptyStateProps | NoResultsEmptyStateProps;

export type NoResultsProps = Omit<NoResultsEmptyStateProps, 'variant' | 'action'>;

/** The no-results glyph: the empty artwork at 40px on a quiet gray. */
const NO_RESULTS_ICON = <EmptyIcon size={40} color="var(--gray-8)" />;

/** 320px: the no-results area. */
const NO_RESULTS_MIN_HEIGHT = 'calc(var(--space-9) * 5)';

export function EmptyState(props: EmptyStateProps) {
  const { title, description, icon, style } = props;
  const variant = props.variant ?? 'panel';
  const noResults = variant === 'no-results';
  const glyph = noResults && icon === undefined ? NO_RESULTS_ICON : icon;
  // Only the panel takes an action, also when a caller bypasses the types.
  const action = props.variant === 'no-results' ? undefined : props.action;
  const TitleTag = props.variant === 'no-results' ? undefined : props.titleAs;

  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="1"
      data-empty-state={variant}
      style={{
        flex: 1,
        minHeight: noResults ? NO_RESULTS_MIN_HEIGHT : 200,
        padding: noResults ? 'var(--space-5)' : 'var(--space-7) var(--space-5)',
        textAlign: 'center',
        ...style,
      }}
    >
      {glyph != null && glyph !== false && (
        <Box
          aria-hidden="true"
          data-empty-state-icon=""
          style={{ marginBottom: noResults ? 'var(--space-3)' : 'var(--space-2)', lineHeight: 0 }}
        >
          {glyph}
        </Box>
      )}
      <Text size="2" weight="medium" asChild={TitleTag != null} style={{ color: 'var(--text-primary)', margin: 0 }}>
        {TitleTag ? <TitleTag>{title}</TitleTag> : title}
      </Text>
      {description != null && description !== false && (
        <Text
          as="div"
          size="2"
          style={{
            color: 'var(--text-secondary)',
            lineHeight: 'var(--line-height-2)',
            maxWidth: noResults ? undefined : 300,
          }}
        >
          {description}
        </Text>
      )}
      {action != null && action !== false && (
        <Box style={{ marginTop: 'var(--space-3)' }}>{action}</Box>
      )}
    </Flex>
  );
}

/** A search or filter that matched nothing. */
export function NoResults({ title, description, icon, style }: NoResultsProps) {
  return <EmptyState variant="no-results" title={title} description={description} icon={icon} style={style} />;
}
