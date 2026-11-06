'use client';

/** An uploaded logo, fitted in a `size` square. In dark mode it sits on a light plate (`--mcp-logo-plate`). */
export function McpLogoImage({ src, size }: { src: string; size: number }) {
  const inset = Math.max(1, Math.round(size / 10));
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={{
        display: 'block',
        flexShrink: 0,
        objectFit: 'contain',
        boxSizing: 'border-box',
        background: 'var(--mcp-logo-plate)',
        padding: `calc(${inset}px * var(--mcp-logo-plate-on))`,
        borderRadius: Math.max(2, Math.round(size / 5)),
      }}
    />
  );
}
