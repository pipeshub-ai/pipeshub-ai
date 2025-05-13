import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import { useState, useEffect } from 'react';
import { Helmet } from 'react-helmet-async';
import domainIcon from '@iconify-icons/mdi/domain';
import accountIcon from '@iconify-icons/mdi/account';

import {
  Card,
  Stack,
  alpha,
  Dialog,
  Typography,
  DialogTitle,
  CardContent,
  DialogContent,
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
      <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ textAlign: 'center', pt: 3 }}>
          <Typography variant="h4" sx={{ fontWeight: 600, mb: 1 }}>
            Choose Account Type
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Select the type of account you want to create
          </Typography>
        </DialogTitle>

        <DialogContent sx={{ pb: 3 }}>
          <Stack spacing={2} direction={{ xs: 'column', sm: 'row' }} sx={{ mt: 2 }}>
            <Card
              sx={{
                flex: 1,
                cursor: 'pointer',
                transition: 'all 0.2s',
                border: '1px solid',
                borderColor: 'divider',
                '&:hover': {
                  borderColor: 'primary.main',
                  boxShadow: (theme) => `0 8px 16px 0 ${alpha(theme.palette.primary.main, 0.1)}`,
                },
              }}
              onClick={() => handleAccountTypeSelect('individual')}
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
                <Icon icon={accountIcon} width={48} height={48} color="#0C4A6E" />
                <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>
                  Individual
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  For personal use or freelancers
                </Typography>
              </CardContent>
            </Card>

            <Card
              sx={{
                flex: 1,
                cursor: 'pointer',
                transition: 'all 0.2s',
                border: '1px solid',
                borderColor: 'divider',
                '&:hover': {
                  borderColor: 'primary.main',
                  boxShadow: (theme) => `0 8px 16px 0 ${alpha(theme.palette.primary.main, 0.1)}`,
                },
              }}
              onClick={() => handleAccountTypeSelect('business')}
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
                <Icon icon={domainIcon} width={48} height={48} color="#0C4A6E" />
                <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>
                  Organization
                </Typography>
                <Typography variant="body2" color="text.secondary">
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
