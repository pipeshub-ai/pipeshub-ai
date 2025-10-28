import type { BoxProps } from '@mui/material/Box';
import type { TypographyProps } from '@mui/material/Typography';

import type { SVGProps } from 'react';
import { forwardRef } from 'react';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { useTheme } from '@mui/material/styles';

import { RouterLink } from 'src/routes/components';

import { logoClasses } from './classes';

// ----------------------------------------------------------------------

type LogoGlyphProps = SVGProps<SVGSVGElement>;

const LogoGlyph = (props: LogoGlyphProps) => (
  <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
    <defs>
      <linearGradient id="logoGradient" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="#8B5CF6" />
        <stop offset="0.45" stopColor="#7C3AED" />
        <stop offset="1" stopColor="#A855F7" />
      </linearGradient>
    </defs>
    <path
      d="M16 32C14.9444 32 13.9722 31.7362 13.0833 31.2083C12.2222 30.7083 11.5278 30.0278 11 29.1667C10.5 28.2778 10.25 27.3056 10.25 26.2499C10.25 24.9166 10.5556 23.8056 11.1667 22.9166C11.7778 22.0278 12.7222 20.9861 14 19.7917C14.9444 18.9306 15.4167 18.111 15.4167 17.3333V16.5834H14.6667C13.8056 16.5834 12.625 17.4445 11.125 19.1667C9.65278 20.889 7.8611 21.7499 5.75 21.7499C4.69445 21.7499 3.72222 21.5 2.83333 21C1.97222 20.4722 1.27778 19.7778 0.75 18.9166C0.25 18.0278 0 17.0555 0 16C0 14.9444 0.25 13.9861 0.75 13.125C1.27778 12.2361 1.97222 11.5417 2.83333 11.0417C3.72222 10.5139 4.69445 10.25 5.75 10.25C7.83333 10.25 9.6111 11.0972 11.0833 12.7917C12.5556 14.4861 13.75 15.3333 14.6667 15.3333H15.4167V14.6667C15.4167 13.8889 14.9444 13.0694 14 12.2083L13.0417 11.3333C12.3472 10.6944 11.7083 9.93056 11.125 9.04166C10.5417 8.12499 10.25 7.02778 10.25 5.75C10.25 4.69445 10.5 3.73611 11 2.87501C11.5278 1.98611 12.2222 1.29167 13.0833 0.791666C13.9722 0.263888 14.9444 0 16 0C17.0555 0 18.0139 0.263888 18.875 0.791666C19.7638 1.31944 20.4584 2.01389 20.9584 2.87501C21.4861 3.73611 21.7499 4.69445 21.7499 5.75C21.7499 7.83333 20.9027 9.6111 19.2083 11.0833C17.5139 12.5556 16.6667 13.75 16.6667 14.6667V15.3333H17.3333C18.2778 15.3333 19.4722 14.4861 20.9166 12.7917C22.3333 11.0972 24.111 10.25 26.2501 10.25C27.3056 10.25 28.2638 10.5139 29.125 11.0417C30.0139 11.5417 30.7083 12.2222 31.2083 13.0833C31.7362 13.9444 32 14.9167 32 16C32 17.0555 31.7362 18.0278 31.2083 18.9166C30.7083 19.7778 30.0139 20.4722 29.125 21C28.2638 21.5 27.3056 21.7499 26.2501 21.7499C24.9445 21.7499 23.8195 21.4306 22.875 20.7917C21.9584 20.1528 20.9306 19.2222 19.7917 18C18.9306 17.0555 18.111 16.5834 17.3333 16.5834H16.6667V17.3333C16.6667 18.3611 17.5139 19.5555 19.2083 20.9166C20.9027 22.2778 21.7499 24.0555 21.7499 26.2499C21.7499 27.3056 21.4861 28.2778 20.9584 29.1667C20.4584 30.0278 19.7778 30.7083 18.9166 31.2083C18.0555 31.7362 17.0834 32 16 32Z"
      fill="url(#logoGradient)"
    />
  </svg>
);

type LogoVariant = 'symbol' | 'wordmark';

export type LogoProps = BoxProps & {
  href?: string;
  disableLink?: boolean;
  variant?: LogoVariant;
  iconSize?: number;
  textVariant?: TypographyProps['variant'];
  spacing?: number;
};

function renderSymbol(size: number) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', width: size, height: size, color: 'inherit' }}>
      <LogoGlyph width="100%" height="100%" />
    </Box>
  );
}

function renderWordmark(size: number, variant: TypographyProps['variant'], spacing: number) {
  return (
    <Box
      component="span"
      sx={(theme) => ({
        display: 'inline-flex',
        alignItems: 'center',
        gap: theme.spacing(spacing),
        lineHeight: 0,
      })}
    >
      {renderSymbol(size)}
      <Box component="span" sx={{ display: 'inline-flex', flexDirection: 'column', lineHeight: 1.1 }}>
        <Typography
          variant={variant}
          sx={(theme) => ({ fontWeight: 600, textTransform: 'none', letterSpacing: theme.typography.caption.letterSpacing })}
        >
          Relationship
        </Typography>
        <Typography
          variant={variant}
          sx={(theme) => ({ fontWeight: 600, textTransform: 'none', letterSpacing: theme.typography.caption.letterSpacing })}
        >
          Intelligence
        </Typography>
      </Box>
    </Box>
  );
}

export const Logo = forwardRef<HTMLDivElement, LogoProps>(
  (
    {
      href = '/',
      disableLink = false,
      variant = 'symbol',
      iconSize,
      textVariant = 'subtitle2',
      spacing = 1,
      width,
      height,
      className,
      sx,
      ...other
    },
    ref
  ) => {
    const theme = useTheme();

    const numericHeight = typeof height === 'number' ? height : undefined;
    const numericWidth = typeof width === 'number' ? width : undefined;

    const resolvedIconSize = iconSize ?? (variant === 'symbol' ? numericHeight ?? numericWidth ?? 32 : 22);

    const content =
      variant === 'symbol'
        ? renderSymbol(resolvedIconSize)
        : renderWordmark(resolvedIconSize, textVariant, spacing);

    const componentProps = disableLink
      ? { component: 'div' as const }
      : { component: RouterLink, href };

    return (
      <Box
        ref={ref}
        {...componentProps}
        className={logoClasses.root.concat(className ? ` ${className}` : '')}
        aria-label="Logo"
        sx={{
          color: theme.palette.text.primary,
          flexShrink: 0,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          textDecoration: 'none',
          lineHeight: 0,
          ...(disableLink && { pointerEvents: 'none' }),
          ...sx,
        }}
        {...other}
      >
        {content}
      </Box>
    );
  }
);

Logo.displayName = 'Logo';
