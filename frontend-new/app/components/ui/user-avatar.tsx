'use client';

import React from 'react';
import { Avatar, Badge } from '@radix-ui/themes';

// ─────────────────────────────────────────────
// UserAvatar — standalone avatar circle
// ─────────────────────────────────────────────

export interface UserAvatarProfile {
  /** Full name (highest priority for initials) */
  fullName?: string | null;
  firstName?: string | null;
  lastName?: string | null;
  /** Email — used as a last resort to derive initials */
  email?: string | null;
}

export interface UserAvatarProps extends UserAvatarProfile {
  /** Profile picture URL — takes precedence over initials when set */
  src?: string | null;
  /** Size in px (default: 32) */
  size?: number;
  /** Radix corner radius (default: "full") */
  radius?: React.ComponentProps<typeof Avatar>['radius'];
}

/**
 * Maps an explicit pixel size to the closest Radix Avatar size token.
 * This prevents a mismatch between the outer container size and internal
 * font/layout sizing that Radix uses when the size prop is fixed.
 */
function toRadixSize(px: number): React.ComponentProps<typeof Avatar>['size'] {
  if (px <= 20) return '1';
  if (px <= 28) return '2';
  if (px <= 36) return '3';
  if (px <= 44) return '4';
  return '5';
}

/**
 * Resolve initials from a profile object.
 * Priority: fullName → firstName+lastName → email local-part → '?'.
 */
export function getInitials({ fullName, firstName, lastName, email }: UserAvatarProfile): string {
  // 1. fullName (e.g. "Rishabh Gupta" → "RG", "sofia.chen" → "SC")
  if (fullName) {
    const parts = fullName.trim().split(/[\s._-]+/);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return fullName.slice(0, 2).toUpperCase();
  }
  // 2. firstName + lastName
  if (firstName || lastName) {
    return [(firstName ?? '')[0], (lastName ?? '')[0]].filter(Boolean).join('').toUpperCase();
  }
  // 3. Email local-part (e.g. "abhishek@pipeshub.com" → "AB")
  if (email) {
    const local = email.split('@')[0];
    const parts = local.trim().split(/[\s._-]+/);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return local.slice(0, 2).toUpperCase();
  }
  return '?';
}

/**
 * UserAvatar — renders a single avatar circle.
 * Falls back to derived initials when `src` is absent or fails to load.
 * Initials resolution priority: fullName → firstName+lastName → email → '?'.
 */
export function UserAvatar({
  fullName,
  firstName,
  lastName,
  email,
  src,
  size = 32,
  radius = 'full',
}: UserAvatarProps) {
  if (src) {
    return (
      <Avatar
        size={toRadixSize(size)}
        variant="soft"
        radius={radius}
        fallback={getInitials({ fullName, firstName, lastName, email })}
        src={src}
        style={{ width: `${size}px`, height: `${size}px`, flexShrink: 0 }}
      />
    );
  }

  return (
    <Badge
      style={{
        backgroundColor: 'var(--accent-a3)',
        color: 'var(--accent-a11)',
        padding: 'var(--space-1)',
        borderRadius: 'var(--radius-2)',
        flexShrink: 0,
        width: 'var(--space-6)',
        height: 'var(--space-6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '14px'
      }}
    >
      {getInitials({ fullName, firstName, lastName, email })}
    </Badge>
  );
}
