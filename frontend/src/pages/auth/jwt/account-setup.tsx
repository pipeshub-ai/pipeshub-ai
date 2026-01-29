import { useNavigate } from 'react-router';
import { useState, useEffect } from 'react';
import { Helmet } from 'react-helmet-async';

import {
  useTheme,
  useMediaQuery,
} from '@mui/material';

import { OrgExists } from 'src/auth/context/jwt';
import AccountSetUpForm from 'src/auth/view/auth/account-setup';

// Account type interface
export type AccountType = 'individual' | 'business';

// ----------------------------------------------------------------------

const metadata = { title: 'Account Setup' };

export default function Page() {
  const accountType: AccountType = 'business';
  const navigate = useNavigate();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const isDarkMode = theme.palette.mode === 'dark';
  
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });

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
        }
      } catch (err) {
        console.error('Error checking if organization exists:', err);
      }
    };

    checkOrgExists();
    // eslint-disable-next-line
  }, []);

  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>

      <AccountSetUpForm accountType={accountType} />
    </>
  );
}