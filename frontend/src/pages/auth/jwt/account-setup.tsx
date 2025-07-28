import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import { useState, useEffect } from 'react';
import { Helmet } from 'react-helmet-async';
import buildingIcon from '@iconify-icons/mdi/domain';
import personIcon from '@iconify-icons/mdi/account-outline';

import {
  Box,
  Card,
  Stack,
  alpha,
  Dialog,
  useTheme,
  Typography,
  DialogTitle,
  CardContent,
  DialogContent,
  useMediaQuery,
} from '@mui/material';

import { OrgExists } from 'src/auth/context/jwt';
import AccountSetUpForm from 'src/auth/view/auth/account-setup';

// Account type interface
export type AccountType = 'individual' | 'business';

// ----------------------------------------------------------------------

const metadata = { title: 'Account Setup' };

export default function Page() {
  const [open, setOpen] = useState(true);
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const navigate = useNavigate();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const isDarkMode = theme.palette.mode === 'dark';
  
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });

  // Show dialog on first load
  useEffect(() => {
    setOpen(true);
  }, []);

  useEffect(() => {
    const checkOrgExists = async () => {
      try {
        const response = await OrgExists();
        if (response.exists === false) {
          setSnackbar({
            open: true,
            message: `Set up account to continue`,
            severity: 'warning',
          });
          navigate('/auth/sign-up');
        } else {
          navigate('/auth/sign-in');
          setOpen(false);
        }
      } catch (err) {
        console.error('Error checking if organization exists:', err);
      }
    };

    checkOrgExists();
    // eslint-disable-next-line
  }, []);

  const handleClose = () => {
    // Don't allow closing without selection
  };

  const handleAccountTypeSelect = (type: AccountType) => {
    setAccountType(type);
    setOpen(false);
  };

  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>

      {/* Account Type Selection Dialog */}
      <Dialog 
        open={open} 
        onClose={handleClose} 
        maxWidth="sm" 
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 1,
            bgcolor: isDarkMode ? 'background.paper' : '#fff',
            boxShadow: isDarkMode ? '0 4px 20px rgba(0, 0, 0, 0.3)' : '0 4px 20px rgba(0, 0, 0, 0.1)',
          }
        }}
      >
        <DialogTitle 
          sx={{ 
            textAlign: 'center', 
            pt: 4,
            pb: 2,
          }}
        >
          <Typography 
            variant="h5" 
            sx={{ 
              fontWeight: 600, 
              mb: 1,
              color: 'text.primary',
            }}
          >
            Choose Account Type
          </Typography>
          <Typography 
            variant="body2" 
            color="text.secondary"
          >
            Select the type of account you want to create
          </Typography>
        </DialogTitle>

        <DialogContent sx={{ px: 3, pb: 4 }}>
          <Stack 
            spacing={2} 
            direction={{ xs: 'column', sm: 'row' }} 
            sx={{ mt: 1 }}
          >
            <Card
              sx={{
                flex: 1,
                cursor: 'pointer',
                transition: 'all 0.2s',
                borderRadius: 1,
                bgcolor: 'background.paper',
                border: '1px solid',
                borderColor: isDarkMode ? 'divider' : theme.palette.grey[200],
                '&:hover': {
                  borderColor: 'primary.main',
                  boxShadow: isDarkMode ? 
                    `0 4px 12px 0 ${alpha(theme.palette.primary.main, 0.15)}` : 
                    `0 4px 12px 0 ${alpha(theme.palette.primary.main, 0.08)}`,
                },
              }}
              onClick={() => handleAccountTypeSelect('individual')}
              elevation={0}
            >
              <CardContent
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  textAlign: 'center',
                  p: 3,
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 48,
                    height: 48,
                    borderRadius: '12px',
                    bgcolor: isDarkMode ? alpha(theme.palette.primary.main, 0.08) : alpha(theme.palette.primary.main, 0.04),
                    mb: 2,
                  }}
                >
                  <Icon 
                    icon={personIcon}
                    width={24}
                    height={24}
                    color={theme.palette.primary.main} 
                  />
                </Box>
                <Typography 
                  variant="subtitle1" 
                  sx={{ 
                    fontWeight: 600, 
                    mb: 0.5,
                  }}
                >
                  Individual
                </Typography>
                <Typography 
                  variant="body2" 
                  color="text.secondary"
                >
                  For personal use or freelancers
                </Typography>
              </CardContent>
            </Card>

            <Card
              sx={{
                flex: 1,
                cursor: 'pointer',
                transition: 'all 0.2s',
                borderRadius: 1,
                bgcolor: 'background.paper',
                border: '1px solid',
                borderColor: isDarkMode ? 'divider' : theme.palette.grey[200],
                '&:hover': {
                  borderColor: 'primary.main',
                  boxShadow: isDarkMode ? 
                    `0 4px 12px 0 ${alpha(theme.palette.primary.main, 0.15)}` : 
                    `0 4px 12px 0 ${alpha(theme.palette.primary.main, 0.08)}`,
                },
              }}
              onClick={() => handleAccountTypeSelect('business')}
              elevation={0}
            >
              <CardContent
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  textAlign: 'center',
                  p: 3,
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 48,
                    height: 48,
                    borderRadius: '12px',
                    bgcolor: isDarkMode ? alpha(theme.palette.primary.main, 0.08) : alpha(theme.palette.primary.main, 0.04),
                    mb: 2,
                  }}
                >
                  <Icon 
                    icon={buildingIcon}
                    width={24}
                    height={24}
                    color={theme.palette.primary.main} 
                  />
                </Box>
                <Typography 
                  variant="subtitle1" 
                  sx={{ 
                    fontWeight: 600, 
                    mb: 0.5,
                  }}
                >
                  Organization
                </Typography>
                <Typography 
                  variant="body2" 
                  color="text.secondary"
                >
                  For companies and teams
                </Typography>
              </CardContent>
            </Card>
          </Stack>
        </DialogContent>
      </Dialog>

      {/* Render the AccountSetUpForm only after account type is selected */}
      {accountType && <AccountSetUpForm accountType={accountType} />}
    </>
  );
}