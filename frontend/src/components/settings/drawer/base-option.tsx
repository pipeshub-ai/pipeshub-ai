import type { ButtonBaseProps } from '@mui/material/ButtonBase';

import infoIcon from '@iconify-icons/eva/info-outline';

import Box from '@mui/material/Box';
import Switch from '@mui/material/Switch';
import Tooltip from '@mui/material/Tooltip';
import ButtonBase from '@mui/material/ButtonBase';

import { varAlpha } from 'src/theme/styles';

import { Iconify } from 'src/components/iconify';


// ----------------------------------------------------------------------

type Props = ButtonBaseProps & {
  icon: string;
  label: string;
  selected: boolean;
  tooltip?: string;
};

export function BaseOption({ icon, label, tooltip, selected, ...other }: Props) {
  return (
    <ButtonBase
      disableRipple
      sx={{
        px: 2,
        py: 2.5,
        borderRadius: 2,
        cursor: 'pointer',
        flexDirection: 'column',
        alignItems: 'flex-start',
        border: (theme) => `solid 1px ${varAlpha(theme.vars.palette.grey['500Channel'], 0.12)}`,
        '&:hover': { bgcolor: (theme) => varAlpha(theme.vars.palette.grey['500Channel'], 0.08) },
        ...(selected && {
          bgcolor: (theme) => varAlpha(theme.vars.palette.grey['500Channel'], 0.08),
        }),
      }}
      {...other}
    >
      <Box
        display="flex"
        alignItems="center"
        justifyContent="space-between"
        sx={{ width: 1, mb: 3 }}
      >
        <Switch name={label} size="small" color="default" checked={selected} sx={{ mr: -0.75 }} />
      </Box>

      <Box display="flex" alignItems="center" justifyContent="space-between" sx={{ width: 1 }}>
        <Box
          component="span"
          sx={{
            lineHeight: '18px',
            fontWeight: 'fontWeightSemiBold',
            fontSize: (theme) => theme.typography.pxToRem(13),
          }}
        >
          {label}
        </Box>

        {tooltip && (
          <Tooltip
            arrow
            title={tooltip}
            slotProps={{
              tooltip: { sx: { maxWidth: 240, mr: 0.5 } },
            }}
          >
            <Iconify
              width={16}
              icon={infoIcon}
              sx={{ cursor: 'pointer', color: 'text.disabled' }}
            />
          </Tooltip>
        )}
      </Box>
    </ButtonBase>
  );
}
