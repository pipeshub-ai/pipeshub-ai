import { useState } from 'react';
import webCheckIcon from '@iconify-icons/mdi/web-check';
import linkVariantIcon from '@iconify-icons/mdi/link-variant';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';

import { alpha, useTheme } from '@mui/material/styles';
import { Box, Grid, Link, Paper, Alert, Container, Typography, IconButton } from '@mui/material';

import { Iconify } from 'src/components/iconify';

import ConfigureServiceDialog from './configure-services-dialog';

// Service configuration list
const SERVICES_LIST = [
  {
    id: 'frontendPublicUrl',
    icon: webCheckIcon,
    title: 'Frontend Public DNS',
    description: 'Publicly accessible frontend service DNS',
    color: '#87CEEB',
    darkModeColor: '#87CEEB', // Same color for dark mode
  },
  {
    id: 'connectorPublicUrl',
    icon: linkVariantIcon,
    title: 'Connectors Public DNS',
    description: 'Publicly accessible connector service DNS',
    color: '#231F20',
    darkModeColor: '#9EA2A9', // Lighter color for dark mode
  },
  // {
  //   id: 'backendNodejs',
  //   icon: 'mdi-nodejs',
  //   title: 'Backend-NodeJS',
  //   description: 'Backend-nodejs-services-url',
  //   color: '#DC382D',
  // },
];

const InternalServicesSettings = () => {
  const theme = useTheme();
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [currentService, setCurrentService] = useState<string | null>(null);

  // Handle service selection
  const handleConfigureService = (serviceId: string) => {
    setCurrentService(serviceId);
    setConfigDialogOpen(true);
  };

  const handleSaveConfiguration = () => {
    // Display appropriate success message
    setConfigDialogOpen(false);
    setCurrentService(null);
  };

  // Helper function to get the appropriate color based on theme mode
  const getServiceColor = (service: (typeof SERVICES_LIST)[0]) =>
    theme.palette.mode === 'dark' && service.darkModeColor ? service.darkModeColor : service.color;

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : theme.palette.background.paper,
        }}
      >
        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 3,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                fontSize: '1.25rem',
                color: theme.palette.text.primary,
              }}
            >
              Internal Services
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              Configure connections to internal services
            </Typography>
          </Box>
        </Box>

        {/* Services Grid */}
        <Grid container spacing={2} mb={3}>
          {SERVICES_LIST.map((service) => {
            const serviceColor = getServiceColor(service);

            return (
              <Grid item xs={12} key={service.id}>
                <Paper
                  elevation={0}
                  sx={{
                    p: 2,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    borderRadius: 1,
                    border: '1px solid',
                    borderColor: theme.palette.divider,
                    bgcolor: 'transparent',
                    transition: 'all 0.15s ease-in-out',
                    '&:hover': {
                      transform: 'translateY(-2px)',
                      boxShadow:
                        theme.palette.mode === 'dark'
                          ? `0 4px 12px ${alpha('#000', 0.15)}`
                          : `0 4px 12px ${alpha(theme.palette.grey[500], 0.1)}`,
                      borderColor: alpha(serviceColor, theme.palette.mode === 'dark' ? 0.4 : 0.4),
                    },
                  }}
                >
                  {/* Service info */}
                  <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
                    <Box
                      sx={{
                        width: 40,
                        height: 40,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mr: 2,
                        bgcolor: alpha(serviceColor, theme.palette.mode === 'dark' ? 0.2 : 0.1),
                        color: serviceColor,
                        borderRadius: 1,
                        flexShrink: 0,
                        border:
                          theme.palette.mode === 'dark' && service.id === 'connectorPublicUrl'
                            ? `1px solid ${alpha(serviceColor, 0.3)}`
                            : 'none',
                      }}
                    >
                      <Iconify icon={service.icon} width={22} height={22} />
                    </Box>

                    <Box>
                      <Typography
                        variant="subtitle1"
                        sx={{
                          fontWeight: 600,
                          fontSize: '0.9375rem',
                        }}
                      >
                        {service.title}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{
                          fontSize: '0.8125rem',
                          lineHeight: 1.5,
                        }}
                      >
                        {service.description}
                      </Typography>
                    </Box>
                  </Box>

                  {/* Settings icon */}
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleConfigureService(service.id);
                    }}
                    sx={{
                      p: 0.75,
                      color: theme.palette.text.secondary,
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.background.paper, 0.3)
                          : alpha(theme.palette.background.default, 0.8),
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      '&:hover': {
                        bgcolor: alpha(
                          theme.palette.primary.main,
                          theme.palette.mode === 'dark' ? 0.15 : 0.08
                        ),
                        color: theme.palette.primary.main,
                      },
                    }}
                    aria-label={`Configure ${service.title}`}
                  >
                    <Iconify icon={settingsIcon} width={18} height={18} />
                  </IconButton>
                </Paper>
              </Grid>
            );
          })}
        </Grid>
      </Paper>

      <ConfigureServiceDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
        onSave={handleSaveConfiguration}
        serviceType={currentService}
      />

      <Alert
        variant="outlined"
        severity="info"
        sx={{
          mt: 3,
          mb: 1,
          borderRadius: 1,
          borderColor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.3 : 0.2),
          '& .MuiAlert-icon': {
            color: theme.palette.info.main,
          },
        }}
      >
        <Typography variant="body2">
          Refer to{' '}
          <Link
            href="https://docs.pipeshub.com/system-overview/internal-services"
            target="_blank"
            rel="noopener"
            sx={{
              color: theme.palette.primary.main,
              textDecoration: 'none',
              fontWeight: 500,
              '&:hover': {
                textDecoration: 'underline',
              },
            }}
          >
            the documentation
          </Link>{' '}
          for more information.
        </Typography>
      </Alert>
    </Container>
  );
};

export default InternalServicesSettings;
