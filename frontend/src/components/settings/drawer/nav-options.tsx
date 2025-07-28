import type { ButtonBaseProps } from '@mui/material/ButtonBase';

import Box from '@mui/material/Box';
import { useTheme } from '@mui/material/styles';

import { varAlpha } from 'src/theme/styles';

import { Block, BlockOption } from './styles';

import type { SettingsState } from '../types';

// ----------------------------------------------------------------------

type Props = {
  value: {
    color: SettingsState['navColor'];
    layout: SettingsState['navLayout'];
  };
  options: {
    colors: SettingsState['navColor'][];
    layouts: SettingsState['navLayout'][];
  };
  onClickOption: {
    color: (newValue: SettingsState['navColor']) => void;
    layout: (newValue: SettingsState['navLayout']) => void;
  };
  hideNavColor?: boolean;
  hideNavLayout?: boolean;
};

export function NavOptions({ options, value, onClickOption, hideNavColor, hideNavLayout }: Props) {
  const theme = useTheme();

  const labelStyles: React.CSSProperties = {
    display: 'block',
    lineHeight: '14px',
    color: 'text.secondary',
    fontWeight: 'fontWeightSemiBold',
    fontSize: theme.typography.pxToRem(11),
  };

  const renderLayout = (
    <Box gap={1.5} display="flex" flexDirection="column">
      <Box component="span" sx={labelStyles}>
        Layout
      </Box>
      <Box gap={1.5} display="flex">
        {options.layouts.map((option) => (
          <LayoutOption
            key={option}
            option={option}
            selected={value.layout === option}
            onClick={() => onClickOption.layout(option)}
          />
        ))}
      </Box>
    </Box>
  );

  const renderColor = (
    <Box gap={1.5} display="flex" flexDirection="column">
      <Box component="span" sx={labelStyles}>
        Color
      </Box>
      <Box gap={1.5} display="flex">
        {options.colors.map((option) => (
          <ColorOption
            key={option}
            option={option}
            selected={value.color === option}
            onClick={() => onClickOption.color(option)}
          />
        ))}
      </Box>
    </Box>
  );

  return (
    <Block title="Nav" tooltip="Dashboard only" sx={{ gap: 2.5 }}>
      {!hideNavLayout && renderLayout}
      {!hideNavColor && renderColor}
    </Block>
  );
}

// ----------------------------------------------------------------------

type OptionProps = ButtonBaseProps & {
  option: string;
  selected: boolean;
};

export function LayoutOption({ option, selected, sx, ...other }: OptionProps) {
  const renderIconNav = () => {
    const baseStyles = {
      flexShrink: 0,
      borderRadius: 1,
      bgcolor: 'currentColor',
    };

    const circle = (
      <Box
        sx={{
          ...baseStyles,
          width: 10,
          height: 10,
          opacity: 0.8,
          ...(selected && { opacity: 1, background: 'var(--active-color)' }),
        }}
      />
    );

    const primaryLine = (
      <Box
        sx={{
          ...baseStyles,
          width: 1,
          height: 4,
          opacity: 0.48,
          ...(option === 'horizontal' && { width: 16 }),
          ...(selected && { background: 'var(--active-color)' }),
        }}
      />
    );

    const secondaryLine = (
      <Box
        sx={{
          ...baseStyles,
          width: 1,
          height: 4,
          maxWidth: 14,
          opacity: 0.24,
          ...(option === 'horizontal' && { maxWidth: 10 }),
          ...(selected && { background: 'var(--active-color)' }),
        }}
      />
    );

    return (
      <Box
        gap={0.5}
        flexShrink={0}
        display="flex"
        flexDirection={option === 'horizontal' ? 'row' : 'column'}
        sx={{
          p: 0.75,
          width: 32,
          height: 1,
          borderRight: 'solid 1px var(--border-color)',
          ...(option === 'mini' && {
            width: 22,
          }),
          ...(option === 'horizontal' && {
            width: 1,
            height: 22,
            borderRight: 'none',
            alignItems: 'center',
            borderBottom: 'solid 1px var(--border-color)',
          }),
        }}
      >
        {circle}
        {primaryLine}
        {secondaryLine}
      </Box>
    );
  };

  const renderIconContent = (
    <Box sx={{ p: 0.5, width: 1, height: 1, flexGrow: 1 }}>
      <Box
        sx={{
          width: 1,
          height: 1,
          opacity: 0.2,
          borderRadius: 0.75,
          bgcolor: 'currentColor',
          ...(selected && { background: 'var(--active-color)' }),
        }}
      />
    </Box>
  );

  const renderIcon = (
    <Box
      display="flex"
      flexDirection={option === 'horizontal' ? 'column' : 'row'}
      sx={(theme) => ({
        width: 1,
        height: 1,
        borderRadius: 'inherit',
        border: `solid 1px ${varAlpha(theme.vars.palette.grey['500Channel'], 0.08)}`,
        ...(selected && {
          borderColor: 'transparent',
        }),
      })}
    >
      {renderIconNav()}
      {renderIconContent}
    </Box>
  );

  return (
    <BlockOption selected={selected} icon={renderIcon} sx={{ height: 64, ...sx }} {...other} />
  );
}

// ----------------------------------------------------------------------

export function ColorOption({ option, selected, sx, ...other }: OptionProps) {
  return (
    <BlockOption
      selected={selected}
      label={option}
      sx={{
        gap: 1.5,
        height: 56,
        textTransform: 'capitalize',
        ...sx,
      }}
      {...other}
    />
  );
}
