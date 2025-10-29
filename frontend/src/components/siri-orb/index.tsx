import type { CSSProperties } from 'react';

import Box, { type BoxProps } from '@mui/material/Box';
import { styled, useTheme } from '@mui/material/styles';

type SiriOrbColorKeys = 'bg' | 'c1' | 'c2' | 'c3';

export type SiriOrbColors = Partial<Record<SiriOrbColorKeys, string>>;

export interface SiriOrbProps extends Omit<BoxProps, 'children'> {
  size?: number | string;
  colors?: SiriOrbColors;
  animationDuration?: number;
}

const SiriOrbRoot = styled(Box, {
  name: 'SiriOrb',
  slot: 'Root',
})(({ theme }) => ({
  position: 'relative',
  display: 'grid',
  gridTemplateAreas: '"stack"',
  overflow: 'hidden',
  borderRadius: '50%',
  background:
    theme.palette.mode === 'dark'
      ? 'radial-gradient(circle, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 30%, transparent 70%)'
      : 'radial-gradient(circle, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.03) 30%, transparent 70%)',
  '&::before': {
    content: '""',
    display: 'block',
    gridArea: 'stack',
    width: '100%',
    height: '100%',
    borderRadius: '50%',
    background: [
      'conic-gradient(from calc(var(--sir-orb-angle) * 1.2) at 30% 65%, var(--sir-orb-c3) 0deg, transparent 45deg 315deg, var(--sir-orb-c3) 360deg)',
      'conic-gradient(from calc(var(--sir-orb-angle) * 0.8) at 70% 35%, var(--sir-orb-c2) 0deg, transparent 60deg 300deg, var(--sir-orb-c2) 360deg)',
      'conic-gradient(from calc(var(--sir-orb-angle) * -1.5) at 65% 75%, var(--sir-orb-c1) 0deg, transparent 90deg 270deg, var(--sir-orb-c1) 360deg)',
      'conic-gradient(from calc(var(--sir-orb-angle) * 2.1) at 25% 25%, var(--sir-orb-c2) 0deg, transparent 30deg 330deg, var(--sir-orb-c2) 360deg)',
      'conic-gradient(from calc(var(--sir-orb-angle) * -0.7) at 80% 80%, var(--sir-orb-c1) 0deg, transparent 45deg 315deg, var(--sir-orb-c1) 360deg)',
      'radial-gradient(ellipse 120% 80% at 40% 60%, var(--sir-orb-c3) 0%, transparent 50%)',
    ].join(','),
    filter: 'blur(var(--sir-orb-blur)) contrast(var(--sir-orb-contrast)) saturate(1.2)',
    animation: 'sirOrbRotate var(--sir-orb-duration) linear infinite',
    transform: 'translateZ(0)',
    willChange: 'transform',
  },
  '&::after': {
    content: '""',
    display: 'block',
    gridArea: 'stack',
    width: '100%',
    height: '100%',
    borderRadius: '50%',
    background:
      'radial-gradient(circle at 45% 55%, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.05) 30%, transparent 60%)',
    mixBlendMode: 'overlay',
  },
  '@property --sir-orb-angle': {
    syntax: '"<angle>"',
    inherits: false,
    initialValue: '0deg',
  },
  '@keyframes sirOrbRotate': {
    from: {
      '--sir-orb-angle': '0deg',
    },
    to: {
      '--sir-orb-angle': '360deg',
    },
  },
}));

export function SiriOrb({
  size = 192,
  colors,
  animationDuration = 20,
  className,
  sx,
  ...other
}: SiriOrbProps) {
  const theme = useTheme();

  const defaultColors: Record<SiriOrbColorKeys, string> = {
    bg: 'transparent',
    c1: theme.palette.primary.light,
    c2: theme.palette.secondary.light,
    c3: theme.palette.info.light,
  };

  const mergedColors = { ...defaultColors, ...colors };

  const resolvedSize = typeof size === 'number' ? `${size}px` : size;
  const numericSize = typeof size === 'number' ? size : parseFloat(size);
  const blurAmount = Math.max(numericSize * 0.08, 8);
  const contrastAmount = Math.max(numericSize * 0.003, 1.8);

  const style = {
    width: resolvedSize,
    height: resolvedSize,
    '--sir-orb-bg': mergedColors.bg,
    '--sir-orb-c1': mergedColors.c1,
    '--sir-orb-c2': mergedColors.c2,
    '--sir-orb-c3': mergedColors.c3,
    '--sir-orb-duration': `${animationDuration}s`,
    '--sir-orb-blur': `${blurAmount}px`,
    '--sir-orb-contrast': contrastAmount,
  } as CSSProperties & Record<string, string | number>;

  return (
    <SiriOrbRoot
      component="div"
      className={className}
      sx={sx}
      style={style}
      {...other}
    />
  );
}

export default SiriOrb;
