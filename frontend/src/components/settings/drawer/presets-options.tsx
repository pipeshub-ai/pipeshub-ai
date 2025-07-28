import Box from '@mui/material/Box';
import ButtonBase from '@mui/material/ButtonBase';
import { alpha as hexAlpha } from '@mui/material/styles';

import { Block } from './styles';

import type { SettingsState } from '../types';

// ----------------------------------------------------------------------

type Value = SettingsState['primaryColor'];

type Props = {
  value: Value;
  options: { name: Value; value: string }[];
  onClickOption: (newValue: Value) => void;
};

export function PresetsOptions({ value, options, onClickOption }: Props) {
  return (
    <Block title="Presets">
      <Box component="ul" gap={1.5} display="grid" gridTemplateColumns="repeat(3, 1fr)">
        {options.map((option) => {
          const selected = value === option.name;

          return (
            <Box component="li" key={option.name} sx={{ display: 'flex' }}>
              <ButtonBase
                onClick={() => onClickOption(option.name)}
                sx={{
                  width: 1,
                  height: 64,
                  borderRadius: 1.5,
                  color: option.value,
                  ...(selected && {
                    bgcolor: hexAlpha(option.value, 0.08),
                  }),
                }}
              >
                <div style={{ width: '28px', height: '28px', backgroundColor: option.value }} />
              </ButtonBase>
            </Box>
          );
        })}
      </Box>
    </Block>
  );
}
