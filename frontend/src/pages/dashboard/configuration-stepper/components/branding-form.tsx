import { useForm, Controller } from 'react-hook-form';
import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { Card, Grid, MenuItem, TextField, Typography, CardContent } from '@mui/material';

export type BrandingFormValues = {
  primaryColor: string;
  secondaryColor: string;
  logoUrl: string;
  fontFamily: string;
  companyName: string;
};

type BrandingFormProps = {
  initialValues?: BrandingFormValues;
  onSubmit: (values: BrandingFormValues) => void;
  getConfig?: () => Promise<any>;
};

const FONT_FAMILIES = ['Inter', 'Roboto', 'Poppins', 'Public Sans', 'Barlow', 'DM Sans'];

const BrandingForm = forwardRef((props: BrandingFormProps, ref) => {
  const { onSubmit, getConfig } = props;
  const [initialLoaded, setInitialLoaded] = useState(false);

  const { control, handleSubmit, getValues, trigger, reset } = useForm<BrandingFormValues>({
    defaultValues: {
      primaryColor: '#6366f1',
      secondaryColor: '#334155',
      logoUrl: '',
      fontFamily: 'Inter',
      companyName: '',
    },
  });

  useEffect(() => {
    const loadConfig = async () => {
      if (getConfig) {
        try {
          const config = await getConfig();
          if (config && Object.keys(config).length > 0) {
            reset(config);
          }
        } catch (error) {
          console.error('Failed to load branding config', error);
        }
      }
      setInitialLoaded(true);
    };
    if (!initialLoaded) {
      loadConfig();
    }
  }, [getConfig, reset, initialLoaded]);

  useImperativeHandle(ref, () => ({
    getFormData: async () => getValues(),
    hasFormData: async () => {
      const values = getValues();
      // Check if any field has been changed from default or is non-empty
      return !!(values.companyName || values.logoUrl || values.primaryColor !== '#6366f1');
    },
    validateForm: async () => trigger(),
    rehydrateForm: (data: BrandingFormValues) => {
      reset(data);
    },
  }));

  return (
    <Card sx={{ p: 3, mt: 3 }}>
      <CardContent>
        <form id="branding-form" onSubmit={handleSubmit(onSubmit)}>
          <Grid container spacing={3}>
            <Grid item xs={12}>
              <Typography variant="h6" gutterBottom>
                Branding Configuration
              </Typography>
              <Typography variant="body2" color="text.secondary" paragraph>
                Customize the look and feel of your application.
              </Typography>
            </Grid>
            <Grid item xs={12} md={6}>
              <Controller
                name="companyName"
                control={control}
                render={({ field }) => (
                  <TextField {...field} fullWidth label="Company Name" placeholder="Acme Corp" />
                )}
              />
            </Grid>

            <Grid item xs={12} md={6}>
              <Controller
                name="logoUrl"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    fullWidth
                    label="Logo URL"
                    placeholder="https://example.com/logo.png"
                  />
                )}
              />
            </Grid>

            <Grid item xs={12} md={6}>
              <Controller
                name="primaryColor"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    fullWidth
                    label="Primary Color"
                    placeholder="#6366f1"
                    InputProps={{
                      endAdornment: (
                        <div
                          style={{
                            width: 24,
                            height: 24,
                            backgroundColor: field.value,
                            border: '1px solid #ccc',
                            borderRadius: 4,
                          }}
                        />
                      ),
                    }}
                  />
                )}
              />
            </Grid>

            <Grid item xs={12} md={6}>
              <Controller
                name="secondaryColor"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    fullWidth
                    label="Secondary Color"
                    placeholder="#334155"
                    InputProps={{
                      endAdornment: (
                        <div
                          style={{
                            width: 24,
                            height: 24,
                            backgroundColor: field.value,
                            border: '1px solid #ccc',
                            borderRadius: 4,
                          }}
                        />
                      ),
                    }}
                  />
                )}
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <Controller
                name="fontFamily"
                control={control}
                render={({ field }) => (
                  <TextField {...field} select fullWidth label="Font Family">
                    {FONT_FAMILIES.map((font) => (
                      <MenuItem key={font} value={font}>
                        <Typography sx={{ fontFamily: font }}>{font}</Typography>
                      </MenuItem>
                    ))}
                  </TextField>
                )}
              />
            </Grid>
          </Grid>
        </form>
      </CardContent>
    </Card>
  );
});

export default BrandingForm;
