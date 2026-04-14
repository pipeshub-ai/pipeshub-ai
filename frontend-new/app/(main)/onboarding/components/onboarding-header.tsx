'use client';

import React from 'react';
import { Flex, Box, Text, Avatar } from '@radix-ui/themes';
import { PipesHubIcon } from '@/app/components/ui';

interface OnboardingHeaderProps {
  // User info (right side)
  userName?: string;
  userEmail?: string;
  userInitials?: string;

  // Org info (left side — shown after org is created)
  orgDisplayName?: string;
  orgSubtitle?: string;
  orgInitial?: string;
  showOrgBadge?: boolean;
}

export function OnboardingHeader({
  userName,
  userEmail,
  userInitials = 'A',
  orgDisplayName,
  orgSubtitle,
  orgInitial = '',
  showOrgBadge = false,
}: OnboardingHeaderProps) {
  return (
    <Box
      style={{
        width: '100%',
      }}
    >
      <Flex
        align="center"
        justify="between"
        style={{
          maxWidth: '1228px',
          width: '100%',
          margin: '0 auto',
          padding: '20px 0',
          minHeight: '80px',
        }}
      >
        {/* Left — Org badge (hidden before org is created) */}
        <Box style={{ width: '220px', flexShrink: 0 }}>
        {showOrgBadge && orgDisplayName ? (
          <Flex align="center" gap="2">
            <Avatar
              size="2"
              fallback={orgInitial || orgDisplayName.charAt(0).toUpperCase()}
              style={{
                backgroundColor: 'var(--accent-a3)',
                color: 'var(--accent-a11)',
                borderRadius: 'var(--radius-2)',
                fontWeight: 600,
              }}
            />
            <Flex direction="column" gap="0">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', lineHeight: '1.3' }}>
                {orgDisplayName}
              </Text>
              {orgSubtitle && (
                <Text size="1" style={{ color: 'var(--gray-9)', lineHeight: '1.3' }}>
                  {orgSubtitle}
                </Text>
              )}
            </Flex>
          </Flex>
        ) : (
          <Box />
        )}
      </Box>

      {/* Center — PipesHub Logo */}
      <Flex align="center" justify="center">
        <PipesHubIcon size={32} color="var(--accent-8)" />
      </Flex>

      {/* Right — User Avatar + Name */}
      <Flex align="center" gap="2" justify="end" style={{ width: '220px', flexShrink: 0 }}>
        {(userName || userEmail) && (
          <>
            <Flex direction="column" align="end" gap="0">
              {userName && (
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', lineHeight: '1.3' }}>
                  {userName}
                </Text>
              )}
              {userEmail && (
                <Text size="1" style={{ color: 'var(--gray-9)', lineHeight: '1.3' }}>
                  {userEmail}
                </Text>
              )}
            </Flex>
            <Avatar
              size="2"
              fallback={userInitials}
              style={{
                backgroundColor: 'var(--accent-a3)',
                color: 'var(--accent-a11)',
                fontWeight: 600,
              }}
            />
          </>
        )}
      </Flex>
      </Flex>
    </Box>
  );
}
