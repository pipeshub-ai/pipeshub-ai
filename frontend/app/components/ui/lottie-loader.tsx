'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Flex, Text } from '@radix-ui/themes';
import { BrandMark } from '@/config';

// Lightweight fallback rendered when the Lottie chunk hasn't loaded yet
// or when it fails to load (e.g. navigation interrupts the download).
function LogoFallback(props: { style?: React.CSSProperties }) {
  const style = { width: 48, height: 48, ...props.style };
  return <BrandMark size={48} style={style} fallback={<img src="/logo/pipes-hub.svg" alt="" style={style} />} />;
}

// @lottiefiles/dotlottie-react accesses `document` at module init — must be
// dynamically imported with ssr:false so it is never evaluated on the server
// during static export pre-rendering.
// The `.catch()` prevents a ChunkLoadError from crashing the app when a
// navigation (e.g. post-login redirect) cancels the chunk download mid-flight.
const DotLottieReact = dynamic(
  () =>
    import('@lottiefiles/dotlottie-react')
      .then((m) => m.DotLottieReact)
      .catch(() => LogoFallback as React.ComponentType<Record<string, unknown>>),
  {
    ssr: false,
    loading: () => <LogoFallback />,
  }
);

// Catches runtime render errors from the Lottie player (e.g. if a lottie file
// is corrupt or the player tears down during unmount).
class LottieErrorBoundary extends React.Component<
  { children: React.ReactNode; fallbackSize: number },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return <LogoFallback style={{ width: this.props.fallbackSize, height: this.props.fallbackSize }} />;
    }
    return this.props.children;
  }
}

export type LottieVariant = 'loader' | 'listening' | 'thinking' | 'still';

interface LottieLoaderProps {
  variant?: LottieVariant;
  size?: number;
  loop?: boolean;
  autoplay?: boolean;
  style?: React.CSSProperties;
  showLabel?: boolean;
  label?: string;
}

const LOTTIE_MAP: Record<LottieVariant, string> = {
  loader: '/lottie-files/Loader.lottie',
  listening: '/lottie-files/Listening.lottie',
  thinking: '/lottie-files/Thinking.lottie',
  still: '/lottie-files/Still.lottie',
};

// The player fetches `src` itself and aborts that fetch when it unmounts, then
// logs the AbortError with console.error. Loaders mount and unmount quickly
// while a chat streams, so each file is fetched once, outside any component,
// and the player gets the bytes.
const lottieDataCache = new Map<string, Promise<ArrayBuffer | null>>();

function loadLottieData(src: string): Promise<ArrayBuffer | null> {
  let pending = lottieDataCache.get(src);
  if (!pending) {
    pending = fetch(src)
      .then((res) => (res.ok ? res.arrayBuffer() : null))
      .catch(() => null)
      .then((data) => {
        if (!data) lottieDataCache.delete(src);
        return data;
      });
    lottieDataCache.set(src, pending);
  }
  return pending;
}

function useLottieData(src: string): ArrayBuffer | null | undefined {
  const [data, setData] = React.useState<ArrayBuffer | null | undefined>(undefined);
  React.useEffect(() => {
    let alive = true;
    setData(undefined);
    void loadLottieData(src).then((loaded) => {
      if (alive) setData(loaded);
    });
    return () => {
      alive = false;
    };
  }, [src]);
  return data;
}

export function LottieLoader({
  variant = 'loader',
  size = 32,
  loop = true,
  autoplay = true,
  style,
  showLabel = false,
  label = 'Loading...',
}: LottieLoaderProps) {
  const data = useLottieData(LOTTIE_MAP[variant]);
  const lottie = !data ? (
    <LogoFallback style={{ width: size, height: size, ...style }} />
  ) : (
    <LottieErrorBoundary fallbackSize={size}>
      <DotLottieReact
        data={data}
        loop={loop}
        autoplay={autoplay}
        style={{
          width: size,
          height: size,
          ...style,
        }}
      />
    </LottieErrorBoundary>
  );

  if (!showLabel) return lottie;

  return (
    <Flex direction="column" align="center" gap="2">
      {lottie}
      <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {label}
      </Text>
    </Flex>
  );
}
