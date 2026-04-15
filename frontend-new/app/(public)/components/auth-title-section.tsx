'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { PipesHubIcon } from '@/app/components/ui';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface AuthTitleSectionProps {
  /** Main heading. Defaults to "Welcome to Pipeshub". */
  title?: string;
  /** Subtitle below the heading. Defaults to the tagline. */
  subtitle?: string;
  /** Bottom margin below the full section. Defaults to "28px". */
  marginBottom?: string;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * AuthTitleSection — reusable logo + heading + subtitle block
 * used at the top of every public-facing auth form.
 *
 * Matches the Figma node shared across sign-in, change-password, SSO, etc.
 */
export default function AuthTitleSection({
  title = 'Welcome to Pipeshub',
  subtitle = "Your organization's knowledge, finally searchable and connected.",
  marginBottom = '28px',
}: AuthTitleSectionProps) {
  return (
    <Box style={{ marginBottom }}>
      {/* ── Logo mark ─────────────────────────────────────────── */}
      <Box style={{ marginBottom: '16px' }}>
       <PipesHubIcon
        size={40}
        color="var(--accent-8)" />
      </Box>

      {/* ── Heading + subtitle ────────────────────────────────── */}
      <Flex direction="column" gap="1">
        <Text
          style={{
            color: 'var(--gray-12)',
            fontSize: '24px',
            fontWeight: 500,
            letterSpacing: '-0.1px',
            lineHeight: '30px',
          }}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text
            style={{
              color: 'var(--gray-11)',
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
            }}
          >
            {subtitle}
          </Text>
        ) : null}
      </Flex>
    </Box>
  );
}
