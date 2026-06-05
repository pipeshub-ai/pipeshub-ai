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

/** First character of a string as a full Unicode code point (emoji-safe), or '' if empty. */
function firstChar(s: string): string {
  for (const char of s) {
    return char;
  }
  return '';
}

/**
 * Derive initials from a free-form name string by splitting on separators.
 * Returns '' when no usable characters remain (caller decides the fallback).
 * e.g. "Acme Corporation" → "AC", "Pipeshub Inc." → "PI", "sofia.chen" → "SC", .
 */
function initialsFromName(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return '';
  if (parts.length === 1) return Array.from(parts[0].toUpperCase()).slice(0, 2).join('');
  return firstChar(parts[0].toUpperCase()) + firstChar(parts[parts.length - 1].toUpperCase());
}

/**
 * Resolve initials from a profile object.
 * Priority: fullName → firstName+lastName → email local-part → '?'.
 * Guards against names made of only separators, leading/trailing separators,
 * whitespace-only values, and multi-byte (emoji) first characters.
 */
export function getInitials({ fullName, firstName, lastName, email }: UserAvatarProfile): string {
  // 1. fullName (e.g. "Acme Corporation" → "AC", "Pipeshub Inc." → "PI")
  const fromFullName = initialsFromName(fullName?.trim() ?? '');
  if (fromFullName) return fromFullName;

  // 2. firstName + lastName
  const first = firstChar((firstName ?? '').trim());
  const last = firstChar((lastName ?? '').trim());
  if (first || last) return (first + last).toUpperCase();

  // 3. Email local-part (e.g. "abhishek@pipeshub.com" → "AB")
  if (email) {
    const fromEmail = initialsFromName(email.split('@')[0].trim());
    if (fromEmail) return fromEmail;
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
