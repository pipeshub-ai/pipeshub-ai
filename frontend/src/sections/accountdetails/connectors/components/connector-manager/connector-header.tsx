import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  IconButton,
  Button,
  Stack,
  Chip,
  alpha,
  useTheme,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import refreshIcon from '@iconify-icons/mdi/refresh';
import arrowBackIcon from '@iconify-icons/mdi/arrow-left';
import { useAccountType } from 'src/hooks/use-account-type';
import { Connector } from '../../types/types';

interface ConnectorHeaderProps {
  connector: Connector;
  loading: boolean;
  onRefresh: () => void;
}

const ConnectorHeader: React.FC<ConnectorHeaderProps> = ({
  connector,
  loading,
  onRefresh,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { isBusiness, loading: accountTypeLoading } = useAccountType();
  const isActive = connector.isActive || false;
  const handleBack = () => {
    if (accountTypeLoading) return; // Avoid navigation until account type is known
    const basePath = isBusiness
      ? '/account/company-settings/settings/connector'
      : '/account/individual/settings/connector';
    navigate(basePath);
  };

  return (
    <Box
      sx={{
        p: 2,
        borderBottom: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
      }}
    >
      <Stack spacing={2}>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <IconButton
            onClick={handleBack}
            disabled={accountTypeLoading}
            sx={{
              color: theme.palette.text.secondary,
              '&:hover': {
                backgroundColor: alpha(theme.palette.text.secondary, 0.08),
              },
            }}
          >
            <Iconify icon={arrowBackIcon} width={20} height={20} />
          </IconButton>

          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: 1.5,
              backgroundColor: alpha(theme.palette.primary.main, 0.1),
              border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <img
              src={connector.iconPath}
              alt={connector.name}
              width={20}
              height={20}
            />
          </Box>
          
          <Box>
            <Typography
              variant="h5"
              sx={{
                fontWeight: 700,
                fontSize: '1.5rem',
                color: theme.palette.text.primary,
                mb: 0.5,
              }}
            >
              Connector Management
            </Typography>
            <Typography
              variant="body2"
              sx={{
                color: theme.palette.text.secondary,
                fontSize: '0.875rem',
              }}
            >
              Manage your {connector.appGroup} integrations
              {isActive && (
                <Chip
                  label="Active"
                  size="small"
                  sx={{
                    ml: 1,
                    height: 20,
                    fontSize: '0.6875rem',
                    fontWeight: 600,
                    backgroundColor:
                      theme.palette.mode === 'dark'
                        ? alpha(theme.palette.success.main, 0.8)
                        : alpha(theme.palette.success.main, 0.1),
                    color:
                      theme.palette.mode === 'dark'
                        ? theme.palette.success.contrastText
                        : theme.palette.success.main,
                    border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                  }}
                />
              )}
            </Typography>
          </Box>

          <Box sx={{ flex: 1 }} />

          <Button
            variant="outlined"
            startIcon={<Iconify icon={refreshIcon} width={16} height={16} />}
            onClick={onRefresh}
            disabled={loading}
            sx={{ textTransform: 'none' }}
          >
            Refresh
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default ConnectorHeader;
