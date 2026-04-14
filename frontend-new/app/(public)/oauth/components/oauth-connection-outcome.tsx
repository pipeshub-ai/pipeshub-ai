'use client';

import { Box, Button, Flex, Text } from '@radix-ui/themes';

const BG = '#0a0a0a';
const SUCCESS_MINT = '#4ade80';
const SUCCESS_ICON_BG = 'rgba(74, 222, 128, 0.12)';
const ERROR_RED = '#f87171';
const ERROR_ICON_BG = 'rgba(248, 113, 113, 0.12)';

export interface OAuthConnectionOutcomeProps {
  variant: 'success' | 'error';
  title: string;
  descriptionLines: string[];
  primaryActionLabel?: string;
  onPrimaryAction?: () => void;
}

export function OAuthConnectionOutcome({
  variant,
  title,
  descriptionLines,
  primaryActionLabel,
  onPrimaryAction,
}: OAuthConnectionOutcomeProps) {
  const isSuccess = variant === 'success';

  return (
    <Flex
      align="center"
      justify="center"
      direction="column"
      gap="4"
      style={{
        minHeight: '100vh',
        width: '100%',
        padding: 'var(--space-6)',
        background: BG,
        textAlign: 'center',
      }}
    >
      <Flex
        align="center"
        justify="center"
        style={{
          width: 40,
          height: 40,
          borderRadius: 8,
          flexShrink: 0,
          background: isSuccess ? SUCCESS_ICON_BG : ERROR_ICON_BG,
        }}
      >
        <span
          className="material-icons-outlined"
          style={{
            fontSize: 22,
            color: isSuccess ? SUCCESS_MINT : ERROR_RED,
            lineHeight: 1,
          }}
          aria-hidden
        >
          {isSuccess ? 'check' : 'warning'}
        </span>
      </Flex>

      <Text
        size="5"
        weight="medium"
        style={{
          color: isSuccess ? SUCCESS_MINT : ERROR_RED,
          letterSpacing: '-0.02em',
        }}
      >
        {title}
      </Text>

      <Flex direction="column" gap="2" style={{ maxWidth: 360 }}>
        {descriptionLines.map((line, i) => (
          <Text key={i} size="2" style={{ color: 'var(--gray-11)' }}>
            {line}
          </Text>
        ))}
      </Flex>

      {!isSuccess && onPrimaryAction && primaryActionLabel ? (
        <Box style={{ marginTop: 'var(--space-2)' }}>
          <Button
            type="button"
            variant="outline"
            color="gray"
            size="2"
            highContrast
            onClick={onPrimaryAction}
            style={{
              borderColor: 'var(--gray-7)',
              color: 'var(--gray-12)',
              minHeight: 40,
            }}
          >
            <Flex align="center" gap="2">
              <span
                className="material-icons-outlined"
                style={{ fontSize: 18, lineHeight: 1 }}
                aria-hidden
              >
                refresh
              </span>
              {primaryActionLabel}
            </Flex>
          </Button>
        </Box>
      ) : null}
    </Flex>
  );
}
