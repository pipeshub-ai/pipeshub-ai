import { Helmet } from 'react-helmet-async';
import { useState, useEffect, useCallback } from 'react';

import { Alert, Snackbar } from '@mui/material';

import axios from 'src/utils/axios';

import { useAdmin } from 'src/context/AdminContext';
import { UserProvider } from 'src/context/UserContext'; // Import useAdmin hook

import { ChatBotView } from 'src/sections/qna/view';

import { useAuthContext } from 'src/auth/hooks';
import { AuthProvider } from 'src/auth/context/jwt';

import FullNameDialog from '../components/full-name-dialog';
import OnBoardingStepper from '../configuration-stepper/components/stepper';

// ----------------------------------------------------------------------

const metadata = { title: `Ask Me Anything` };

// Wrapper component to access auth context
function PageContent() {
  const { user } = useAuthContext();
  const { isAdmin } = useAdmin(); // Get admin status
  const [configDialog, setConfigDialog] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [onboardingStatus, setOnboardingStatus] = useState<string | null>(null);
  const [fullNameDialog, setFullNameDialog] = useState<boolean>(false);
  const [isProcessingFullName, setIsProcessingFullName] = useState<boolean>(false);

  // Snackbar state
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'info' | 'warning',
  });

  // Function to fetch onboarding status - using useCallback to prevent recreating on every render
  const fetchOnboardingStatus = useCallback(async () => {
    try {
      setIsLoading(true);
      const response = await axios.get(`/api/v1/org/onboarding-status`);
      const { status } = response.data;

      if (!status) {
        throw new Error('No status found in the response');
      }

      // Store the status in state
      setOnboardingStatus(status);

      // Only show the configuration dialog if:
      // 1. Status is notConfigured
      // 2. User is an admin
      if (status === 'notConfigured' && isAdmin) {
        setConfigDialog(true);
        // If config dialog is shown, don't show fullNameDialog at the same time
        setFullNameDialog(false);
      } else {
        setConfigDialog(false);
        // Now check if we need to show the fullNameDialog
        // Only do this if we have user data
        if (user && (!user.fullName || user.fullName.trim() === '')) {
          setFullNameDialog(true);
          setSnackbar({
            open: true,
            message: 'Please set your full name to continue',
            severity: 'info',
          });
        }
      }

      return status;
    } catch (error) {
      console.error('Error while fetching the onboarding status:', error);
      // If there's an error, show configuration dialog only if admin
      if (isAdmin) {
        setConfigDialog(true);
        // Don't show fullNameDialog if configDialog is shown
        setFullNameDialog(false);
      } else if (user && (!user.fullName || user.fullName.trim() === '')) {
        // For non-admin users with missing full name
        setFullNameDialog(true);
        setSnackbar({
          open: true,
          message: 'Please set your full name to continue',
          severity: 'info',
        });
      }
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [user, isAdmin]);

  // Handle closing the configuration dialog
  const handleClose = useCallback(() => {
    // Just close the dialog - status updates are handled inside the ConfigurationStepper
    // via explicit button actions now
    setConfigDialog(false);

    // Refetch the status to make sure we have the latest status
    // This is important since the ConfigurationStepper may have updated it
    fetchOnboardingStatus();
  }, [fetchOnboardingStatus]);

  const handleOpen = useCallback(() => {
    // Only allow opening config dialog if admin
    if (isAdmin) {
      setConfigDialog(true);
      // Don't show fullNameDialog when configDialog is shown
      setFullNameDialog(false);
    }
  }, [isAdmin]);

  // Handle full name dialog callbacks
  const handleFullNameSuccess = (message: string) => {
    // Mark that we're processing the full name update
    setIsProcessingFullName(true);

    setSnackbar({
      open: true,
      message,
      severity: 'success',
    });
  };

  const handleFullNameError = (message: string) => {
    setSnackbar({
      open: true,
      message,
      severity: 'error',
    });
  };

  const handleFullNameDialogClose = useCallback(() => {
    // Only allow closing if user has a full name set
    if (user?.fullName && user.fullName.trim() !== '') {
      setFullNameDialog(false);
    }
  }, [user]);

  // Close snackbar
  const handleCloseSnackbar = () => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  useEffect(() => {
    // Fetch onboarding status when component mounts
    fetchOnboardingStatus();

    // Only set up beforeunload event if not processing full name
    let handleBeforeUnload: ((e: BeforeUnloadEvent) => any) | null = null;

    if (!isProcessingFullName) {
      handleBeforeUnload = (e: BeforeUnloadEvent) => {
        if (configDialog || fullNameDialog) {
          // Standard way to show a confirmation dialog when leaving the page
          e.preventDefault();
          e.returnValue = ''; // Chrome requires returnValue to be set
          return ''; // This message is typically ignored by browsers for security reasons
        }
        return undefined; // Add a return here to satisfy the consistent-return rule
      };

      window.addEventListener('beforeunload', handleBeforeUnload);
    }

    // Clean up event listener when component unmounts
    return () => {
      if (handleBeforeUnload) {
        window.removeEventListener('beforeunload', handleBeforeUnload);
      }
    };
  }, [fetchOnboardingStatus, configDialog, fullNameDialog, isProcessingFullName]);

  // Check if user has full name and show dialog if not (only if config dialog is not showing)
  useEffect(() => {
    // Only check once loading is complete and we have user data
    // Don't show fullNameDialog if configDialog is already showing
    if (!isLoading && user && !configDialog) {
      // If user doesn't have a full name or it's empty, show the dialog
      if (!user.fullName || user.fullName.trim() === '') {
        setFullNameDialog(true);
        setSnackbar({
          open: true,
          message: 'Please set your full name to continue',
          severity: 'info',
        });
      } else {
        setFullNameDialog(false);
      }
    }
  }, [isLoading, user, configDialog]);

  return (
    <>
      {/* Only render ConfigurationStepper if user is admin */}
      {isAdmin && <OnBoardingStepper open={configDialog} onClose={handleClose} />}

      {/* Full Name Dialog - shown to all users */}
      <FullNameDialog
        open={fullNameDialog}
        onClose={handleFullNameDialogClose}
        onSuccess={handleFullNameSuccess}
        onError={handleFullNameError}
      />

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>

      {!isLoading && <ChatBotView />}
    </>
  );
}

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <AuthProvider>
        <UserProvider>
          <PageContent />
        </UserProvider>
      </AuthProvider>
    </>
  );
}
