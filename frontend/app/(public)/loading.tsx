'use client';

import { Flex } from '@radix-ui/themes';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

export default function Loading() {
  return (
    <Flex
      align="center"
      justify="center"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'var(--olive-1)',
        zIndex: 9999,
      }}
    >
      <LottieLoader variant="loader" size={96} showLabel />
    </Flex>
  );
}
