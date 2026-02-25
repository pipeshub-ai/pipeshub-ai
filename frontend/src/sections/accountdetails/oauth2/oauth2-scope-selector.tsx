import { useTheme } from '@mui/material/styles';
import {
  Box,
  Stack,
  Divider,
  Checkbox,
  FormGroup,
  Typography,
  FormControlLabel,
} from '@mui/material';

import type { ScopeCategory } from './services/oauth2-api';

interface OAuth2ScopeSelectorProps {
  scopesByCategory: ScopeCategory;
  allowedScopes: string[];
  onChange: (scopes: string[]) => void;
  /** Optional max width for the stack (e.g. 640) */
  maxWidth?: number;
}

export function OAuth2ScopeSelector({
  scopesByCategory,
  allowedScopes,
  onChange,
  maxWidth = 640,
}: OAuth2ScopeSelectorProps) {
  const theme = useTheme();
  const entries = Object.entries(scopesByCategory) as [
    string,
    Array<{ name: string; description: string }>,
  ][];
  const allScopeNames = entries.flatMap(([, scopes]) =>
    Array.isArray(scopes) ? scopes.map((s) => s.name) : []
  );
  const allSelected =
    allScopeNames.length > 0 &&
    allScopeNames.every((scopeName) => allowedScopes.includes(scopeName));
  const someSelected = allScopeNames.some((scopeName) => allowedScopes.includes(scopeName));

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      onChange([...allScopeNames]);
    } else {
      onChange([]);
    }
  };

  const handleCategoryChange = (categoryScopeNames: string[], checked: boolean) => {
    if (checked) {
      onChange([
        ...allowedScopes.filter((x) => !categoryScopeNames.includes(x)),
        ...categoryScopeNames,
      ]);
    } else {
      onChange(allowedScopes.filter((x) => !categoryScopeNames.includes(x)));
    }
  };

  const handleScopeChange = (scopeName: string, checked: boolean) => {
    if (checked) {
      onChange([...allowedScopes, scopeName]);
    } else {
      onChange(allowedScopes.filter((x) => x !== scopeName));
    }
  };

  return (
    <Stack spacing={2.5} sx={{ maxWidth }}>
      <FormGroup>
        <FormControlLabel
          control={
            <Checkbox
              checked={allSelected}
              indeterminate={someSelected && !allSelected}
              onChange={(e) => handleSelectAll(e.target.checked)}
            />
          }
          label={
            <Typography sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
              Select all scopes
            </Typography>
          }
        />
      </FormGroup>
      <Divider sx={{ my: 1.5 }} />
      {entries.map(([category, scopeList]) => {
        const safeList = Array.isArray(scopeList) ? scopeList : [];
        const namesInCategory = safeList.map((s) => s.name);
        const selectedInCategory = namesInCategory.filter((n) => allowedScopes.includes(n));
        const categoryAllChecked =
          namesInCategory.length > 0 && selectedInCategory.length === namesInCategory.length;
        const categorySomeChecked = selectedInCategory.length > 0;
        return (
          <Box key={category} sx={{ mb: 1.5 }}>
            <FormGroup sx={{ mb: 0.75 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={categoryAllChecked}
                    indeterminate={categorySomeChecked && !categoryAllChecked}
                    onChange={(e) => handleCategoryChange(namesInCategory, e.target.checked)}
                  />
                }
                label={
                  <Typography
                    sx={{
                      fontWeight: 600,
                      fontSize: '0.875rem',
                      color: theme.palette.text.primary,
                    }}
                  >
                    {category}
                  </Typography>
                }
              />
            </FormGroup>
            <FormGroup sx={{ pl: 3.5, mt: 0 }}>
              {safeList.map((s) => (
                <FormControlLabel
                  key={s.name}
                  control={
                    <Checkbox
                      checked={allowedScopes.includes(s.name)}
                      onChange={(e) => handleScopeChange(s.name, e.target.checked)}
                    />
                  }
                  label={
                    <Box>
                      <Typography
                        component="span"
                        sx={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}
                      >
                        {s.name}
                      </Typography>
                      {s.description && (
                        <Typography
                          component="span"
                          sx={{
                            display: 'block',
                            fontSize: '0.75rem',
                            color: theme.palette.text.secondary,
                          }}
                        >
                          {s.description}
                        </Typography>
                      )}
                    </Box>
                  }
                />
              ))}
            </FormGroup>
          </Box>
        );
      })}
    </Stack>
  );
}
