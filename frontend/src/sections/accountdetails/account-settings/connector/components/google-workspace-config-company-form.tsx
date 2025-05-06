import infoOutlineIcon from '@iconify-icons/eva/info-outline';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import trashOutlineIcon from '@iconify-icons/eva/trash-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
import uploadOutlineIcon from '@iconify-icons/eva/upload-outline';
import downloadOutlineIcon from '@iconify-icons/eva/download-outline';
import fileTextOutlineIcon from '@iconify-icons/mdi/file-text-outline';
import checkmarkCircleOutlineIcon from '@iconify-icons/eva/checkmark-circle-outline';
import { useRef, useState, useEffect, forwardRef, useCallback, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Link,
  Alert,
  Paper,
  Stack,
  Button,
  Tooltip,
  TextField,
  Typography,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { getConnectorPublicUrl } from '../../services/utils/services-configuration-service';

interface GoogleWorkspaceConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  onFileRemoved?: () => void;
  isEnabled?: boolean;
}

export interface GoogleWorkspaceConfigFormRef {
  handleSave: () => Promise<boolean>;
}

export const GoogleWorkspaceConfigForm = forwardRef<
  GoogleWorkspaceConfigFormRef,
  GoogleWorkspaceConfigFormProps
>(({ onValidationChange, onSaveSuccess, onFileRemoved, isEnabled }, ref) => {
  const theme = useTheme();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [enableRealTimeUpdates, setEnableRealTimeUpdates] = useState(false);
  const [skipValidation, setSkipValidation] = useState(false);
  const [topicName, setTopicName] = useState('');
  const [topicNameError, setTopicNameError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [formEditMode, setFormEditMode] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileUploadError, setFileUploadError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [hasExistingFile, setHasExistingFile] = useState(false);
  const [adminEmail, setAdminEmail] = useState<string>('');
  const [adminEmailError, setAdminEmailError] = useState<string | null>(null);
  const [webhookBaseUrl, setWebhookBaseUrl] = useState('');

  // Store original values for cancel operation
  const [originalState, setOriginalState] = useState({
    adminEmail: '',
    topicName: '',
    enableRealTimeUpdates: false,
    selectedFile: null as File | null,
    fileName: null as string | null,
  });

  const validateTopicName = useCallback(
    (name: string): boolean => {
      if (enableRealTimeUpdates && !name.trim()) {
        setTopicNameError('Topic name is required when real-time updates are enabled');
        return false;
      }

      setTopicNameError(null);
      return true;
    },
    [enableRealTimeUpdates]
  );

  // Validate email format
  const validateEmail = useCallback((email: string): boolean => {
    if (!email) {
      setAdminEmailError('Google Workspace Admin email is required');
      return false;
    }

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const isValid = emailPattern.test(email);

    if (!isValid) {
      setAdminEmailError('Please enter a valid email address');
      return false;
    }

    setAdminEmailError(null);
    return true;
  }, []);

  // Validate form based on file and admin email
  const validateForm = useCallback(
    (hasFile: boolean, email: string, enableUpdates: boolean, topic: string) => {
      const isFileValid = hasFile;
      const isEmailValid = validateEmail(email);
      const isTopicValid = !enableUpdates || validateTopicName(topic);

      onValidationChange(isFileValid && isEmailValid && isTopicValid);
      return isFileValid && isEmailValid && isTopicValid;
    },
    [onValidationChange, validateEmail, validateTopicName]
  );

  // Enable edit mode
  const handleEnterEditMode = () => {
    // Store original values before editing
    setOriginalState({
      adminEmail,
      topicName,
      enableRealTimeUpdates,
      selectedFile,
      fileName,
    });

    setFormEditMode(true);
    setIsEditing(false); // Reset isEditing to make sure validation works during edit mode
  };

  // Cancel edit mode and restore original values
  const handleCancelEdit = () => {
    setAdminEmail(originalState.adminEmail);
    setTopicName(originalState.topicName);
    setEnableRealTimeUpdates(originalState.enableRealTimeUpdates);
    setSelectedFile(originalState.selectedFile);
    setFileName(originalState.fileName);

    // Clear any errors
    setAdminEmailError(null);
    setTopicNameError(null);
    setFileUploadError(null);

    setFormEditMode(false);
    setIsEditing(false);

    // Re-validate with original values
    validateForm(
      hasExistingFile,
      originalState.adminEmail,
      originalState.enableRealTimeUpdates,
      originalState.topicName
    );
  };

  // Load existing configuration on component mount
  useEffect(() => {
    if (!isEditing && !formEditMode) {
      const fetchExistingConfig = async () => {
        try {
          const response = await axios.get('/api/v1/connectors/credentials', {
            params: {
              service: 'googleWorkspace',
            },
          });

          if (response.data && response.data.isConfigured) {
            setHasExistingFile(true);
            if (response.data.fileName) {
              setFileName(response.data.fileName);
            } else {
              setFileName('google-workspace-credentials.json');
            }
            if (Object.prototype.hasOwnProperty.call(response.data, 'enableRealTimeUpdates')) {
              // Convert the value to an actual boolean before setting state
              const realTimeUpdatesEnabled =
                typeof response.data.enableRealTimeUpdates === 'string'
                  ? response.data.enableRealTimeUpdates.toLowerCase() === 'true'
                  : Boolean(response.data.enableRealTimeUpdates);

              setEnableRealTimeUpdates(realTimeUpdatesEnabled);
              if (response.data.topicName) {
                setTopicName(response.data.topicName);
              }
            }
            // Set admin email if it exists in the response
            if (response.data.adminEmail) {
              setAdminEmail(response.data.adminEmail);
            }

            // Store original values
            setOriginalState({
              adminEmail: response.data.adminEmail || '',
              topicName: response.data.topicName || '',
              enableRealTimeUpdates: response.data.enableRealTimeUpdates || false,
              selectedFile: null,
              fileName: response.data.fileName || 'google-workspace-credentials.json',
            });

            validateForm(
              true,
              response.data.adminEmail || '',
              response.data?.enableRealTimeUpdates,
              response.data?.topicName
            );
          }
        } catch (error) {
          console.error('Error fetching existing configuration:', error);
          // If error, assume no config exists
          setHasExistingFile(false);
        } finally {
          setIsLoading(false);
        }
      };

      fetchExistingConfig();
    }
  }, [validateForm, formEditMode, isEditing]);

  // Expose the handleSave method to the parent component
  useImperativeHandle(ref, () => ({
    handleSave,
  }));

  useEffect(() => {
    const fetchConnectorUrl = async () => {
      try {
        const config = await getConnectorPublicUrl();
        if (config?.url) {
          setWebhookBaseUrl(config.url);
        }
      } catch (error) {
        console.error('Failed to load connector URL', error);
        // Fallback to window location if we can't get the connector URL
        setWebhookBaseUrl(window.location.origin);
      }
    };

    fetchConnectorUrl();
  }, []);

  // Update validation state when file is selected or removed or admin email changes
  useEffect(() => {
    // Always validate during edit mode, or when not editing
    if (formEditMode || !isEditing) {
      validateForm(hasExistingFile || !!selectedFile, adminEmail, enableRealTimeUpdates, topicName);
    }
  }, [
    formEditMode,
    isEditing,
    selectedFile,
    hasExistingFile,
    adminEmail,
    enableRealTimeUpdates,
    topicName,
    validateForm,
  ]);

  // Handle admin email change
  const handleAdminEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && hasExistingFile) {
      // If trying to edit when not in edit mode, enter edit mode first
      handleEnterEditMode();
    }

    setIsEditing(true);
    const email = e.target.value;
    setAdminEmail(email);
    validateEmail(email);
    validateForm(hasExistingFile || !!selectedFile, email, enableRealTimeUpdates, topicName);
  };

  // Handle file upload click
  const handleUploadClick = () => {
    if (!formEditMode && hasExistingFile) {
      // If trying to upload when not in edit mode, enter edit mode first
      handleEnterEditMode();
    }

    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  // Handle file selection
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsEditing(true);
    const file = e.target.files?.[0];
    if (!file) {
      if (!hasExistingFile) {
        setSelectedFile(null);
        setFileName(null);
        setFileUploadError(null);
        validateForm(false, adminEmail, enableRealTimeUpdates, topicName);
      }
      return;
    }

    setFileName(file.name);
    setFileUploadError(null);

    // Check file type
    if (file.type !== 'application/json' && !file.name.endsWith('.json')) {
      setFileUploadError('Only JSON files are accepted.');
      setSelectedFile(null);
      if (!hasExistingFile) {
        validateForm(false, adminEmail, enableRealTimeUpdates, topicName);
      }
      return;
    }

    setSelectedFile(file);
    validateForm(true, adminEmail, enableRealTimeUpdates, topicName);
  };

  const handleRealTimeUpdatesChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && hasExistingFile) {
      // If trying to change when not in edit mode, enter edit mode first
      handleEnterEditMode();
    }

    setIsEditing(true);
    const { checked } = event.target;
    setEnableRealTimeUpdates(checked);

    if (!checked) {
      // Clear topic name error when disabling
      setTopicNameError(null);
    } else {
      // Validate topic name when enabling
      validateTopicName(topicName);
    }

    validateForm(hasExistingFile || !!selectedFile, adminEmail, checked, topicName);
  };

  // Handle topic name change
  const handleTopicNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && hasExistingFile) {
      // If trying to change when not in edit mode, enter edit mode first
      handleEnterEditMode();
    }

    setIsEditing(true);
    const name = e.target.value;
    setTopicName(name);
    validateTopicName(name);
    validateForm(hasExistingFile || !!selectedFile, adminEmail, enableRealTimeUpdates, name);
  };

  // Handle file download
  const handleDownload = async () => {
    if (selectedFile) {
      // If we have a file in memory, download it directly
      const url = URL.createObjectURL(selectedFile);
      const a = document.createElement('a');
      a.href = url;
      a.download = selectedFile.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } else if (hasExistingFile) {
      // If we have a file on the server, fetch and download it
      try {
        setIsLoading(true);
        const response = await axios.get('/api/v1/connectors/credentials/download', {
          params: {
            service: 'googleWorkspace',
          },
          responseType: 'blob',
        });

        const url = URL.createObjectURL(new Blob([response.data]));
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName || 'google-workspace-credentials.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (error) {
        console.error('Error downloading file:', error);
        setSaveError(`Failed to download configuration file":${error.message}`);
      } finally {
        setIsLoading(false);
      }
    }
  };

  // Handle save - upload the file directly
  const handleSave = async (): Promise<boolean> => {
    // Validate form before saving
    if (
      !validateForm(hasExistingFile || !!selectedFile, adminEmail, enableRealTimeUpdates, topicName)
    ) {
      return false;
    }

    // If no new file is selected and we already have a config, just update admin email and real-time settings
    if (!selectedFile && hasExistingFile) {
      try {
        setIsSaving(true);
        setSaveError(null);

        // Send admin email update and real-time settings
        await axios.post('/api/v1/connectors/credentials', {
          service: 'googleWorkspace',
          adminEmail,
          enableRealTimeUpdates,
          topicName: enableRealTimeUpdates ? topicName : '',
          filechanged: false,
        });

        if (onSaveSuccess) {
          onSaveSuccess();
        }

        // Update original state with new values after successful save
        setOriginalState({
          adminEmail,
          topicName,
          enableRealTimeUpdates,
          selectedFile: null,
          fileName,
        });

        // Exit edit mode
        setFormEditMode(false);
        setIsEditing(false);

        return true;
      } catch (error) {
        console.error('Error updating Google Workspace settings:', error);
        setSaveError(`Failed to update Google Workspace settings: ${error.message}`);
        return false;
      } finally {
        setIsSaving(false);
      }
    }

    // Rest of your existing handleSave function...
    if (!selectedFile) {
      setFileUploadError('Please select a JSON file to upload.');
      return false;
    }

    setIsSaving(true);
    setSaveError(null);

    try {
      // Create FormData with the file and admin email
      const formData = new FormData();

      // Add the file
      formData.append('googleWorkspaceCredentials', selectedFile);

      // Add the admin email
      formData.append('adminEmail', adminEmail);
      formData.append('fileChanged', 'true');
      // Add real-time updates settings
      formData.append('enableRealTimeUpdates', enableRealTimeUpdates.toString());
      if (enableRealTimeUpdates) {
        formData.append('topicName', topicName);
      }

      // Send the file to the backend
      await axios.post('/api/v1/connectors/credentials', formData, {
        params: {
          service: 'googleWorkspace',
        },
        // Let axios set the Content-Type header with correct boundary
      });

      setHasExistingFile(true);

      // Update original state with new values after successful save
      setOriginalState({
        adminEmail,
        topicName,
        enableRealTimeUpdates,
        selectedFile: null,
        fileName,
      });

      // Exit edit mode
      setFormEditMode(false);
      setIsEditing(false);

      if (onSaveSuccess) {
        onSaveSuccess();
      }

      return true;
    } catch (error) {
      console.error('Error uploading Google Workspace config:', error);
      setSaveError(`Failed to upload Google Workspace configuration file: ${error.message}`);
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  // Handle file removal
  const handleRemoveFile = () => {
    if (!formEditMode && hasExistingFile) {
      // If trying to remove when not in edit mode, enter edit mode first
      handleEnterEditMode();
    }

    if (hasExistingFile && !selectedFile) {
      // If removing a server-side file
      setIsLoading(true);

      axios
        .delete('/api/v1/connectors/credentials', {
          params: {
            service: 'googleWorkspace',
          },
        })
        .then(() => {
          setHasExistingFile(false);
          setFileName(null);
          setAdminEmail('');
          setEnableRealTimeUpdates(false);
          setFormEditMode(false); // Exit edit mode after successful deletion
          validateForm(false, adminEmail, enableRealTimeUpdates, topicName);
          if (onFileRemoved) {
            onFileRemoved();
          }
        })

        .catch((error) => {
          console.error('Error removing configuration:', error);
          setSaveError(`Failed to remove configuration ${error.message}`);
        })
        .finally(() => {
          setIsLoading(false);
        });
    } else {
      // If removing a locally selected file
      setSelectedFile(null);
      if (!hasExistingFile) {
        setFileName(null);
        validateForm(false, adminEmail, enableRealTimeUpdates, topicName);
      }

      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <>
      <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
        Refer to{' '}
        <Link
          href="https://docs.pipeshub.com/enterprise/connectors/googleWorkspace"
          target="_blank"
          rel="noopener"
        >
          the documentation
        </Link>{' '}
        for more information.
      </Alert>
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <>
          {/* Header with Edit button */}
          {hasExistingFile && (
            <Box
              sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}
            >
              <Typography variant="h6">Google Workspace Configuration</Typography>

              {!formEditMode ? (
                !isEnabled ? (
                  <span>
                    <Button
                      variant="contained"
                      startIcon={<Iconify icon={editOutlineIcon} width={18} height={18} />}
                      disabled={isEnabled}
                      onClick={handleEnterEditMode}
                    >
                      Edit Configuration
                    </Button>
                  </span>
                ) : (
                  <Tooltip title="Disable the connector before editing it" placement="top">
                    <span>
                      <Button
                        variant="contained"
                        startIcon={<Iconify icon={editOutlineIcon} width={18} height={18} />}
                        disabled={isEnabled}
                        onClick={handleEnterEditMode}
                      >
                        Edit Configuration
                      </Button>
                    </span>
                  </Tooltip>
                )
              ) : (
                <Stack direction="row" spacing={1}>
                  <Button
                    variant="outlined"
                    startIcon={<Iconify icon={closeOutlineIcon} width={18} height={18} />}
                    onClick={handleCancelEdit}
                    color="inherit"
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="contained"
                    startIcon={<Iconify icon={saveOutlineIcon} width={18} height={18} />}
                    onClick={handleSave}
                    color="primary"
                  >
                    Save Changes
                  </Button>
                </Stack>
              )}
            </Box>
          )}

          {saveError && (
            <Alert
              severity="error"
              sx={{
                mb: 3,
                borderRadius: 1,
              }}
            >
              {saveError}
            </Alert>
          )}

          <Box
            sx={{
              mb: 3,
              p: 2,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.info.main, 0.04),
              border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 1,
            }}
          >
            <Iconify
              icon={infoOutlineIcon}
              width={20}
              height={20}
              color={theme.palette.info.main}
              style={{ marginTop: 2 }}
            />
            <Box>
              <Typography variant="body2" color="text.secondary">
                To configure Google Workspace integration, you need to upload your Services
                credentials JSON file from the{' '}
                <Link
                  href="https://console.cloud.google.com/iam-admin/serviceaccounts/"
                  target="_blank"
                  rel="noopener"
                  sx={{ fontWeight: 500 }}
                >
                  Google Cloud Console
                </Link>
                .
              </Typography>
            </Box>
          </Box>

          {/* Google Workspace Admin Email Field */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Google Workspace Admin Email
            </Typography>
            <TextField
              fullWidth
              required
              value={adminEmail}
              onChange={handleAdminEmailChange}
              placeholder="admin@yourdomain.com"
              error={!!adminEmailError}
              helperText={adminEmailError}
              disabled={hasExistingFile && !formEditMode}
              sx={{ mb: 1 }}
            />
            <Typography variant="caption" color="text.secondary">
              Enter the email address of a Google Workspace administrator with appropriate
              permissions
            </Typography>
          </Box>

          {/* File Upload Section */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              {hasExistingFile ? 'Google Workspace Credentials' : 'Upload Credentials JSON'}
            </Typography>

            <Paper
              variant="outlined"
              sx={{
                p: 2,
                display: 'flex',
                flexDirection: { xs: 'column', sm: 'row' },
                alignItems: 'center',
                justifyContent: 'space-between',
                borderColor: alpha(theme.palette.primary.main, 0.2),
                borderStyle: hasExistingFile ? 'solid' : 'dashed',
                borderRadius: 1,
                bgcolor: hasExistingFile
                  ? alpha(theme.palette.success.main, 0.04)
                  : alpha(theme.palette.primary.main, 0.02),
                gap: 2,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 40,
                    height: 40,
                    borderRadius: '8px',
                    bgcolor: hasExistingFile
                      ? alpha(theme.palette.success.main, 0.08)
                      : alpha(theme.palette.primary.main, 0.08),
                  }}
                >
                  <Iconify
                    icon={hasExistingFile ? checkmarkCircleOutlineIcon : fileTextOutlineIcon}
                    width={24}
                    height={24}
                    color={
                      hasExistingFile ? theme.palette.success.main : theme.palette.primary.main
                    }
                  />
                </Box>

                <Box>
                  <Typography variant="subtitle2">
                    {fileName || 'No file selected'}
                    {hasExistingFile && !selectedFile && ' (Saved)'}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {hasExistingFile && !selectedFile
                      ? 'Google Workspace credentials are configured'
                      : 'Upload your Google Cloud OAuth credentials JSON file'}
                  </Typography>
                </Box>
              </Box>

              {hasExistingFile || selectedFile ? (
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={handleDownload}
                    startIcon={<Iconify icon={downloadOutlineIcon} width={18} height={18} />}
                    color="primary"
                    sx={{
                      minWidth: 120,
                      flexShrink: 0,
                    }}
                  >
                    Download
                  </Button>
                  {(formEditMode || !hasExistingFile) && (
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={handleRemoveFile}
                      startIcon={<Iconify icon={trashOutlineIcon} width={18} height={18} />}
                      color="error"
                      sx={{
                        minWidth: 120,
                        flexShrink: 0,
                      }}
                    >
                      Remove
                    </Button>
                  )}
                  {(formEditMode || !hasExistingFile) && hasExistingFile && !selectedFile && (
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={handleUploadClick}
                      startIcon={<Iconify icon={editOutlineIcon} width={18} height={18} />}
                      sx={{
                        minWidth: 120,
                        flexShrink: 0,
                      }}
                    >
                      Replace
                    </Button>
                  )}
                </Stack>
              ) : (
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleUploadClick}
                  startIcon={<Iconify icon={uploadOutlineIcon} width={18} height={18} />}
                  sx={{
                    minWidth: 120,
                    flexShrink: 0,
                  }}
                >
                  Upload JSON
                </Button>
              )}

              <input
                ref={fileInputRef}
                type="file"
                accept="application/json"
                onChange={handleFileChange}
                style={{ display: 'none' }}
              />
            </Paper>

            {fileUploadError && (
              <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
                {fileUploadError}
              </Typography>
            )}
          </Box>

          {/* Real-time Gmail Updates Section */}
          <Box sx={{ mb: 3 }}>
            <Paper
              variant="outlined"
              sx={{
                p: 2.5,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.background.default, 0.8),
                borderColor: alpha(theme.palette.divider, 0.2),
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'flex-start', mb: 1.5 }}>
                <Box
                  component="label"
                  htmlFor="realtime-updates-checkbox"
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    cursor: formEditMode || !hasExistingFile ? 'pointer' : 'default',
                  }}
                >
                  <Box
                    component="input"
                    type="checkbox"
                    id="realtime-updates-checkbox"
                    checked={enableRealTimeUpdates}
                    onChange={handleRealTimeUpdatesChange}
                    disabled={hasExistingFile && !formEditMode}
                    sx={{
                      mr: 1.5,
                      width: 20,
                      height: 20,
                      cursor: formEditMode || !hasExistingFile ? 'pointer' : 'default',
                    }}
                  />
                  <Typography variant="subtitle2">Enable Real-time Gmail Updates</Typography>
                </Box>
              </Box>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2, pl: 3.5 }}>
                By enabling this feature, you will receive real-time updates for new emails in your
                Google Workspace. This requires a valid Google Pub/Sub topic name.
              </Typography>

              {enableRealTimeUpdates && (
                <Box sx={{ pl: 3.5 }}>
                  <Box
                    sx={{
                      p: 2,
                      mt: 1,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.info.main, 0.04),
                      border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 1,
                    }}
                  >
                    <Iconify
                      icon={infoOutlineIcon}
                      width={20}
                      height={20}
                      color={theme.palette.info.main}
                      style={{ marginTop: 2 }}
                    />
                    <Typography variant="body2" color="text.secondary">
                      When creating your Pub/Sub topic, set the endpoint URL as{' '}
                      <Typography component="span" variant="body2" fontWeight="bold">
                        &quot;{webhookBaseUrl}/gmail/webhook&quot;
                      </Typography>
                    </Typography>
                  </Box>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Google Pub/Sub Topic Name
                  </Typography>
                  <TextField
                    fullWidth
                    required
                    value={topicName}
                    onChange={handleTopicNameChange}
                    placeholder="projects/your-project/topics/your-topic"
                    error={!!topicNameError}
                    disabled={hasExistingFile && !formEditMode}
                    helperText={
                      topicNameError ||
                      'Enter the Google Pub/Sub topic that will receive Gmail notifications'
                    }
                    size="small"
                    sx={{ mb: 2 }}
                  />
                </Box>
              )}
            </Paper>
          </Box>

          {isSaving && (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
              <CircularProgress size={24} />
            </Box>
          )}
        </>
      )}
    </>
  );
});

export default GoogleWorkspaceConfigForm;
