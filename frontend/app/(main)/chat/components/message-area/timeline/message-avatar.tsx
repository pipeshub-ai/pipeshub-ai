'use client';

import { Avatar } from '@radix-ui/themes';
import { getInitials } from '@/app/components/ui/user-avatar';

export const AVATAR_SIZE_DESKTOP = 24;
export const AVATAR_SIZE_MOBILE = 20;

interface MessageAvatarProps {
  name: string;
  /** Initials come from the name; an AI responder's colour is jade, as on the composer's picker. */
  tone: 'person' | 'ai';
  size: number;
  src?: string | null;
}

export function MessageAvatar({ name, tone, size, src }: MessageAvatarProps) {
  return (
    <span data-testid="message-avatar" aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
      <Avatar
        size="1"
        radius="full"
        variant="soft"
        color={tone === 'ai' ? 'jade' : undefined}
        src={src ?? undefined}
        fallback={getInitials({ fullName: name })}
        style={{ width: size, height: size, fontSize: size <= AVATAR_SIZE_MOBILE ? 9 : 10 }}
      />
    </span>
  );
}
