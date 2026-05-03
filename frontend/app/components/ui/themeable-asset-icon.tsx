'use client';

/**
 * Theme-aware static brand / connector artwork.
 *
 * - Local same-origin SVG (`/` path, not `http`): may be inlined when the file uses
 *   `currentColor`, so the parent `color` CSS prop controls the glyph (same idea as file-type icons).
 * - Otherwise: plain `<img>` in a neutral box (no tile) by default. Use `variant="tiled"` for a padded
 *   square behind the image when needed.
 *
 * Use `mode="bitmap"` to skip fetch (remote `http(s)` URLs or when you want synchronous render only).
 */

import React, { useEffect, useState } from 'react';

// ---------------------------------------------------------------------------
// URL + SVG helpers (pure)
// ---------------------------------------------------------------------------

function isHttpOrHttpsUrl(src: string): boolean {
  return /^https?:\/\//i.test(src.trim());
}

function isLocalPath(src: string): boolean {
  const t = src.trim();
  return t.startsWith('/') && !isHttpOrHttpsUrl(t);
}

function isSvgPath(src: string): boolean {
  return src.trim().toLowerCase().endsWith('.svg');
}

function stripScriptsRegex(s: string): string {
  const re = /<script\b[^>]*>[\s\S]*?<\/script\s*>/gi;
  let prev: string;
  do {
    prev = s;
    s = s.replace(re, '');
  } while (s !== prev);
  return s;
}

function stripScripts(s: string): string {
  if (typeof DOMParser === 'undefined') {
    return stripScriptsRegex(s);
  }
  try {
    const doc = new DOMParser().parseFromString(s, 'image/svg+xml');
    if (doc.querySelector('parsererror')) {
      return stripScriptsRegex(s);
    }
    doc.querySelectorAll('script').forEach((el) => el.remove());
    const root = doc.documentElement;
    if (!root || root.tagName.toLowerCase() !== 'svg') {
      return stripScriptsRegex(s);
    }
    return new XMLSerializer().serializeToString(root);
  } catch {
    return stripScriptsRegex(s);
  }
}

function svgMarkupUsesCurrentColor(svgText: string): boolean {
  return svgText.toLowerCase().includes('currentcolor');
}

function buildInlineSvgMarkup(svgText: string, size: number): string | null {
  let t = stripScripts(svgText).trim();
  if (!t.toLowerCase().startsWith('<svg')) return null;
  t = t.replace(/\swidth="[^"]*"/gi, '').replace(/\sheight="[^"]*"/gi, '');
  return t.replace(
    /<svg\b/i,
    `<svg width="${size}" height="${size}" focusable="false" aria-hidden="true" style="display:block" `
  );
}

// ---------------------------------------------------------------------------
// Fetch cache (dedupe across palette rows / chips)
// ---------------------------------------------------------------------------

const svgTextCache = new Map<string, Promise<string>>();

function loadSvgTextOnce(src: string): Promise<string> {
  const existing = svgTextCache.get(src);
  if (existing) return existing;
  const p = fetch(src).then((r) => {
    if (!r.ok) throw new Error(String(r.status));
    return r.text();
  });
  svgTextCache.set(src, p);
  return p;
}

// ---------------------------------------------------------------------------
// Outer box (flat vs optional tile)
// ---------------------------------------------------------------------------

function tileOuterSize(size: number, tilePadding: number): number {
  return size + tilePadding * 2;
}

function outerIconBoxStyle(params: {
  size: number;
  variant: 'flat' | 'tiled';
  tilePadding: number;
  tileBackground: string;
  tileBorder: string;
}): React.CSSProperties {
  if (params.variant === 'flat') {
    return {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      width: params.size,
      minWidth: params.size,
      height: params.size,
      boxSizing: 'border-box',
      flexShrink: 0,
      lineHeight: 0,
    };
  }
  const outer = tileOuterSize(params.size, params.tilePadding);
  return {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: outer,
    minWidth: outer,
    height: outer,
    boxSizing: 'border-box',
    borderRadius: 'var(--radius-1)',
    background: params.tileBackground,
    border: `1px solid ${params.tileBorder}`,
    flexShrink: 0,
  };
}

// ---------------------------------------------------------------------------
// Presentational subcomponents
// ---------------------------------------------------------------------------

type BitmapImgProps = {
  src: string;
  size: number;
  className?: string;
  fallbackSrc?: string;
  variant: 'flat' | 'tiled';
  tilePadding: number;
  tileBackground: string;
  tileBorder: string;
};

function BitmapImg({
  src,
  size,
  className,
  fallbackSrc,
  variant,
  tilePadding,
  tileBackground,
  tileBorder,
}: BitmapImgProps) {
  return (
    <span
      className={className}
      style={outerIconBoxStyle({ size, variant, tilePadding, tileBackground, tileBorder })}
    >
      <img
        src={src}
        width={size}
        height={size}
        alt=""
        style={{ objectFit: 'contain', display: 'block' }}
        onError={(e) => {
          if (!fallbackSrc) return;
          e.currentTarget.onerror = null;
          e.currentTarget.src = fallbackSrc;
        }}
      />
    </span>
  );
}

type InlineSvgProps = {
  html: string;
  size: number;
  color: string;
  className?: string;
};

function InlineSvgMarkUp({ html, size, color, className }: InlineSvgProps) {
  return (
    <span
      className={className}
      style={{
        color,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        lineHeight: 0,
        width: size,
        height: size,
      }}
      // Same-origin `/assets/...` only; callers must not pass untrusted remote SVG text.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export type ThemeableAssetIconMode = 'auto' | 'bitmap';

export type ThemeableAssetIconVariant = 'flat' | 'tiled';

export type ThemeableAssetIconProps = {
  src: string;
  size: number;
  /** Used when SVG is inlined with `currentColor`. */
  color?: string;
  className?: string;
  fallbackSrc?: string;
  /**
   * `auto` — local `/` SVG: try `currentColor` inline, else `<img>`. Non-SVG or `bitmap`: `<img>` only (no fetch).
   * Remote `http(s)` is treated as `bitmap` internally.
   */
  mode?: ThemeableAssetIconMode;
  /** `flat` (default): no square behind the glyph. `tiled`: padded square + border (legacy). */
  variant?: ThemeableAssetIconVariant;
  tilePadding?: number;
  tileBackground?: string;
  tileBorder?: string;
};

type AutoSvgBodyProps = Omit<ThemeableAssetIconProps, 'mode'> & {
  variant: ThemeableAssetIconVariant;
  tilePadding: number;
  tileBackground: string;
  tileBorder: string;
};

function AutoLocalSvg({
  src,
  size,
  color,
  className,
  fallbackSrc,
  variant,
  tilePadding,
  tileBackground,
  tileBorder,
}: AutoSvgBodyProps) {
  const [phase, setPhase] = useState<'loading' | 'inline' | 'bitmap'>('loading');
  const [inlineHtml, setInlineHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPhase('loading');
    setInlineHtml(null);

    loadSvgTextOnce(src)
      .then((text) => {
        if (cancelled) return;
        if (svgMarkupUsesCurrentColor(text)) {
          const html = buildInlineSvgMarkup(text, size);
          if (html) {
            setInlineHtml(html);
            setPhase('inline');
            return;
          }
        }
        setPhase('bitmap');
      })
      .catch(() => {
        if (!cancelled) setPhase('bitmap');
      });

    return () => {
      cancelled = true;
    };
  }, [src, size]);

  if (phase === 'loading') {
    return (
      <span
        className={className}
        style={{
          ...outerIconBoxStyle({ size, variant, tilePadding, tileBackground, tileBorder }),
          opacity: 0.35,
        }}
        aria-hidden
      />
    );
  }

  if (phase === 'inline' && inlineHtml) {
    return <InlineSvgMarkUp html={inlineHtml} size={size} color={color} className={className} />;
  }

  return (
    <BitmapImg
      src={src}
      size={size}
      className={className}
      fallbackSrc={fallbackSrc}
      variant={variant}
      tilePadding={tilePadding}
      tileBackground={tileBackground}
      tileBorder={tileBorder}
    />
  );
}

// ---------------------------------------------------------------------------
// Public presets + root component
// ---------------------------------------------------------------------------

/** Shared `color` defaults for agent-builder surfaces (spread onto `ThemeableAssetIcon`). Icons render flat (no tile). */
export const themeableAssetIconPresets = {
  agentBuilderSidebar: {
    color: 'var(--olive-11)',
  },
  agentBuilderCategoryRow: {
    color: 'var(--slate-11)',
  },
  flowNodeHeader: {
    color: 'var(--slate-11)',
  },
  flowNodeWell: {
    color: 'var(--agent-flow-text-muted)',
  },
} as const satisfies Record<string, Partial<ThemeableAssetIconProps>>;

const DEFAULT_COLOR = 'var(--slate-11)';
const DEFAULT_TILE_BG = 'var(--gray-2)';
const DEFAULT_TILE_BORDER = 'var(--gray-6)';
const DEFAULT_TILE_PADDING = 3;

export function ThemeableAssetIcon({
  src,
  size,
  color = DEFAULT_COLOR,
  className,
  fallbackSrc,
  mode = 'auto',
  variant = 'flat',
  tilePadding = DEFAULT_TILE_PADDING,
  tileBackground = DEFAULT_TILE_BG,
  tileBorder = DEFAULT_TILE_BORDER,
}: ThemeableAssetIconProps) {
  const useBitmapOnly =
    mode === 'bitmap' || isHttpOrHttpsUrl(src) || !isSvgPath(src) || !isLocalPath(src);

  if (useBitmapOnly) {
    return (
      <BitmapImg
        src={src}
        size={size}
        className={className}
        fallbackSrc={fallbackSrc}
        variant={variant}
        tilePadding={tilePadding}
        tileBackground={tileBackground}
        tileBorder={tileBorder}
      />
    );
  }

  return (
    <AutoLocalSvg
      src={src}
      size={size}
      color={color}
      className={className}
      fallbackSrc={fallbackSrc}
      variant={variant}
      tilePadding={tilePadding}
      tileBackground={tileBackground}
      tileBorder={tileBorder}
    />
  );
}
