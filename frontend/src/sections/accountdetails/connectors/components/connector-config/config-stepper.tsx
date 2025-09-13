import React from 'react';
import {
  Stepper,
  Step,
  StepLabel,
  Box,
  alpha,
  useTheme,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import checkIcon from '@iconify-icons/mdi/check';

interface ConfigStepperProps {
  activeStep: number;
  steps: string[];
}

const ConfigStepper: React.FC<ConfigStepperProps> = ({ activeStep, steps }) => {
  const theme = useTheme();

  return (
    <Stepper
      activeStep={activeStep}
      sx={{
        mb: 3,
        '& .MuiStepLabel-root': {
          padding: '6px 0',
        },
        '& .MuiStepLabel-label': {
          fontSize: '0.8125rem',
          fontWeight: 600,
          color: theme.palette.text.secondary,
        },
        '& .MuiStepLabel-label.Mui-active': {
          color: theme.palette.primary.main,
        },
        '& .MuiStepLabel-label.Mui-completed': {
          color: theme.palette.text.primary,
        },
        '& .MuiStepIcon-root': {
          fontSize: '1.25rem',
          '&.Mui-active': {
            color: theme.palette.primary.main,
          },
          '&.Mui-completed': {
            color: theme.palette.primary.main,
          },
        },
        '& .MuiStepConnector-line': {
          borderColor: alpha(theme.palette.divider, 0.4),
        },
      }}
    >
      {steps.map((label, index) => (
        <Step key={label}>
          <StepLabel
            StepIconComponent={({ active, completed }) => (
              <Box
                sx={{
                  width: 28,
                  height: 28,
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  bgcolor:
                    active || completed
                      ? theme.palette.primary.main
                      : alpha(theme.palette.grey[500], 0.15),
                  color:
                    active || completed
                      ? theme.palette.primary.contrastText
                      : theme.palette.text.secondary,
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  transition: 'all 0.2s ease-in-out',
                  border: `2px solid ${
                    active || completed
                      ? theme.palette.primary.main
                      : alpha(theme.palette.grey[500], 0.15)
                  }`,
                }}
              >
                {completed ? <Iconify icon={checkIcon} width={14} height={14} /> : index + 1}
              </Box>
            )}
          >
            {label}
          </StepLabel>
        </Step>
      ))}
    </Stepper>
  );
};

export default ConfigStepper;
