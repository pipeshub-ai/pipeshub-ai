'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Text } from '@radix-ui/themes';
import { useTip, useTipsStore } from '@/lib/store/tips-store';

const ROTATING_TIPS = ['noteOnly', 'assistantAnywhere', 'agentAnywhere', 'agentAccess'] as const;
const ROTATE_MS = 8000;

/** Advances on every open of the popover, so repeat users see a different tip each time. */
let nextStart = 0;

/**
 * Footer of the mention popover. The first time it ever opens it explains the keys (`popoverIntro`, marked
 * seen once shown); after that it rotates through the usage tips.
 */
export function ComposerTips() {
  const { t } = useTranslation();
  const intro = useTip('mentions.popoverIntro');
  const markSeen = useTipsStore((s) => s.markSeen);
  const [showIntro] = useState(intro.visible);
  const start = useRef<number | null>(null);
  start.current ??= nextStart++;
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (showIntro) markSeen('mentions.popoverIntro');
  }, [showIntro, markSeen]);

  useEffect(() => {
    const id = setInterval(() => setOffset((o) => o + 1), ROTATE_MS);
    return () => clearInterval(id);
  }, []);

  const key = showIntro && offset === 0 ? 'intro' : ROTATING_TIPS[(start.current + offset) % ROTATING_TIPS.length];
  return (
    <Text
      as="div"
      size="1"
      role="note"
      data-testid="composer-tip"
      data-tip={key}
      style={{ color: 'var(--slate-11)', padding: '6px 8px 2px', borderTop: '1px solid var(--slate-5)', marginTop: 4 }}
    >
      {t(`chat.mentions.tips.${key}`)}
    </Text>
  );
}
