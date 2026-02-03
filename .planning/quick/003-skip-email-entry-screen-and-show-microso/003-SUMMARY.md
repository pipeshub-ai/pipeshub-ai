# Quick Task 003: Skip Email Entry Screen and Show Microsoft SSO Button Directly

## Summary

Fixed the skipEmailScreen feature that was implemented in quick tasks 001 and 002 but wasn't working due to an API path mismatch.

## Problem

The user had enabled the "Skip Email Entry Screen" toggle in the admin settings, but the login page still showed the email entry screen instead of the direct Microsoft SSO button.

**Root Cause:** The frontend was calling the wrong API endpoint.
- Frontend called: `/api/v1/auth/directSsoConfig`
- Backend expected: `/api/v1/userAccount/directSsoConfig`

This path mismatch caused the request to return HTML (the SPA fallback) instead of the expected JSON response, which prevented the skipEmailScreen feature from working.

## Solution

Fixed the API path in `frontend/src/auth/view/auth/authentication-view.tsx` line 460:
- Before: `axios.get('/api/v1/auth/directSsoConfig')`
- After: `axios.get('/api/v1/userAccount/directSsoConfig')`

## Files Modified

| File | Change |
|------|--------|
| `frontend/src/auth/view/auth/authentication-view.tsx` | Fixed API endpoint path |

## Commits

| Hash | Message |
|------|---------|
| 099fd12c | fix(quick-003): correct API path for directSsoConfig endpoint |

## Verification

After deploying the frontend fix:
1. Navigate to the login page
2. Open browser DevTools (F12) > Console tab
3. You should now see:
   - `[Auth] SSO Config Response: {skipEmailScreen: true, microsoft: {...}}`
   - `[Auth] Activating direct Microsoft SSO mode`
4. The login page should show the Microsoft SSO button directly (no email entry screen)

## Related

- Quick task 001: Implemented skipEmailScreen feature
- Quick task 002: Fixed navigation loop bug
- Quick task 003: Fixed API path mismatch (this task)
