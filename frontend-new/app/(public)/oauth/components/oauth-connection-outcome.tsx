'use client';

import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

const BG = '#0a0a0a';
const SUCCESS_MINT = '#4ade80';
const SUCCESS_ICON_BG = 'rgba(74, 222, 128, 0.12)';
const ERROR_RED = '#f87171';
const ERROR_ICON_BG = 'rgba(248, 113, 113, 0.12)';
const DENIED_AMBER = '#fb923c';
const DENIED_ICON_BG = 'rgba(251, 146, 60, 0.14)';

export interface ScopePermissionRow {
  name: string;
  granted: boolean;
}

export interface OAuthConnectionOutcomeProps {
  variant: 'success' | 'error' | 'denied';
  title: string;
  descriptionLines: string[];
  primaryActionLabel?: string;
  onPrimaryAction?: () => void;
  scopePermissionSummary?: ScopePermissionRow[];
}

export function OAuthConnectionOutcome({
  variant,
  title,
  descriptionLines,
  primaryActionLabel,
  onPrimaryAction,
  scopePermissionSummary,
}: OAuthConnectionOutcomeProps) {
  const { t } = useTranslation();
  const isSuccess = variant === 'success';
  const isDenied = variant === 'denied';
  const isPositiveOutcome = isSuccess || isDenied;

  const accentColor = isSuccess ? SUCCESS_MINT : isDenied ? DENIED_AMBER : ERROR_RED;
  const iconBg = isSuccess ? SUCCESS_ICON_BG : isDenied ? DENIED_ICON_BG : ERROR_ICON_BG;
  const iconName = isSuccess ? 'check' : isDenied ? 'block' : 'warning';

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
          background: iconBg,
        }}
      >
        <span
          className="material-icons-outlined"
          style={{
            fontSize: 22,
            color: accentColor,
            lineHeight: 1,
          }}
          aria-hidden
        >
          {iconName}
        </span>
      </Flex>

      <Text
        size="5"
        weight="medium"
        style={{
          color: accentColor,
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

      {!isPositiveOutcome && onPrimaryAction && primaryActionLabel ? (
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
