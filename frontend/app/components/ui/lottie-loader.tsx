'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { useTranslation } from 'react-i18next';
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
  /** What is loading, announced to screen readers; visible with `showLabel`. Defaults to "Loading…". */
  label?: string;
  /**
   * Announce the load to screen readers as a polite status named by `label`
   * ("Loading…" by default). Off unless asked for: many loaders are art (the
   * chat home's brand mark) or sit beside words that already say what is
   * happening. Passing `label` or `showLabel` asks for it too.
   */
  announce?: boolean;
  /** Never announce, whatever else is passed (a toast's glyph tile). */
  decorative?: boolean;
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

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/** Whether the reader asked for less motion; follows the setting while mounted. */
function usePrefersReducedMotion(): boolean {
  const supported = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function';
  // Read at once, so a reader who asked for less motion never fetches the moving file.
  const [reduced, setReduced] = React.useState(() => supported() && window.matchMedia(REDUCED_MOTION_QUERY).matches);
  React.useEffect(() => {
    if (!supported()) return;
    const mql = window.matchMedia(REDUCED_MOTION_QUERY);
    setReduced(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener?.('change', onChange);
    return () => mql.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
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
  label,
  announce,
  decorative = false,
}: LottieLoaderProps) {
  const { t } = useTranslation();
  const resolvedLabel = label ?? t('loading.default');
  // Under reduced motion every variant shows the still frame, not playing.
  const reducedMotion = usePrefersReducedMotion();
  const shownVariant: LottieVariant = reducedMotion ? 'still' : variant;
  const playing = !reducedMotion;
  const data = useLottieData(LOTTIE_MAP[shownVariant]);
  const lottie = !data ? (
    <LogoFallback style={{ width: size, height: size, ...style }} />
  ) : (
    <LottieErrorBoundary fallbackSize={size}>
      <DotLottieReact
        data={data}
        loop={playing && loop}
        autoplay={playing && autoplay}
        style={{
          width: size,
          height: size,
          ...style,
        }}
      />
    </LottieErrorBoundary>
  );

  // Announces only when the caller asks. The listening and thinking marks sit
  // beside their own words, so a label alone does not make them a live region.
  const asked = announce ?? (label !== undefined || showLabel);
  const announces = asked && !decorative && (variant === 'loader' || announce === true);

  if (!showLabel) {
    return (
      <span
        {...(announces ? { role: 'status', 'aria-label': resolvedLabel } : { 'aria-hidden': true })}
        style={{ display: 'contents' }}
      >
        {lottie}
      </span>
    );
  }

  return (
    <Flex
      direction="column"
      align="center"
      gap="2"
      {...(announces ? { role: 'status', 'aria-label': resolvedLabel } : {})}
    >
      {lottie}
      <Text size="2" weight="medium" style={{ color: 'var(--text-secondary)' }}>
        {resolvedLabel}
      </Text>
    </Flex>
  );
}
