---
phase: quick
plan: 002
type: execute
wave: 1
depends_on: []
files_modified:
  - frontend/src/auth/view/auth/authentication-view.tsx
  - backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
autonomous: true

must_haves:
  truths:
    - "When skipEmailScreen is enabled, users see Microsoft SSO button directly without email form"
    - "The directSsoConfig endpoint returns correct skipEmailScreen value from stored config"
    - "Frontend navigation does not cause infinite loop or wrong routing"
  artifacts:
    - path: "frontend/src/auth/view/auth/authentication-view.tsx"
      provides: "Fixed direct SSO routing logic"
    - path: "backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts"
      provides: "Debug logging for directSsoConfig"
  key_links:
    - from: "frontend/src/auth/view/auth/authentication-view.tsx"
      to: "/api/v1/auth/directSsoConfig"
      via: "axios GET request on mount"
      pattern: "axios\\.get.*directSsoConfig"
---

<objective>
Debug and fix the skip email screen feature for direct Microsoft SSO

Purpose: The skipEmailScreen toggle was implemented but is not working in production. Need to identify why the feature isn't activating when enabled and fix the root cause.

Output: Working direct Microsoft SSO flow that skips the email entry screen when the admin toggle is enabled
</objective>

<execution_context>
@C:\Users\isuru\.claude/get-shit-done/workflows/execute-plan.md
@C:\Users\isuru\.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/001-skip-email-screen-direct-microsoft-sso/001-SUMMARY.md
@frontend/src/auth/view/auth/authentication-view.tsx
@backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
@backend/nodejs/apps/src/modules/configuration_manager/controller/cm_controller.ts
</context>

<tasks>

<task type="auto">
  <name>Task 1: Debug and diagnose the skipEmailScreen flow</name>
  <files>
    backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
    frontend/src/auth/view/auth/authentication-view.tsx
  </files>
  <action>
    This is a debugging task. The skipEmailScreen feature was implemented but is not working after deployment. There are several potential failure points to investigate:

    **Potential Issues:**

    1. **Backend getDirectSsoConfig not returning correct data:**
       - The endpoint finds an org with Microsoft auth but configManagerResponse.data might not have skipEmailScreen
       - The skipEmailScreen field might not be in the encrypted config
       - Add debug logging in getDirectSsoConfig method (around line 1866-1907)

    2. **Frontend routing issue:**
       - The useEffect navigates to `/auth/sign-in` when skipEmailScreen is false
       - If user is ALREADY on `/auth/sign-in`, this creates redundant navigation
       - The component might be rendered but state not updating correctly

    3. **Configuration not persisted correctly:**
       - Check if cm_controller.ts saveMicrosoftConfig (line 547-563) is storing skipEmailScreen
       - The encrypted config should include: `{ clientId, tenantId, authority, enableJit, skipEmailScreen }`

    **Debugging Steps:**

    1. Add console.log/logger.debug in getDirectSsoConfig to see:
       - Whether orgAuthConfig is found
       - What configManagerResponse.data contains
       - Whether skipEmailScreen is true/false in the config

    2. In authentication-view.tsx useEffect:
       - Add console.log to see what ssoConfigResponse.data contains
       - Check if the condition `ssoConfigResponse.data?.skipEmailScreen && ssoConfigResponse.data?.microsoft` is true

    3. Check if the issue is the encrypted config not having skipEmailScreen stored when admin saves

    **Fix the identified issue** - Most likely causes in order of probability:

    A. The admin saved the config BEFORE skipEmailScreen was added to the save function
       - Fix: Admin needs to re-save the Microsoft config after the feature was deployed
       - This is a data migration issue, not a code bug

    B. Frontend navigation logic causing component to re-render before state updates
       - Fix: Don't navigate to /auth/sign-in if already on that route
       - Add check: `if (window.location.pathname !== '/auth/sign-in')`

    C. The encrypted config was saved but skipEmailScreen defaulted to undefined (not false)
       - Fix: Ensure the getDirectSsoConfig properly checks `skipEmailScreen === true` not just truthy

    **Implementation:**

    In `userAccount.controller.ts` getDirectSsoConfig method (around line 1892):
    - Add logging BEFORE the `if (configManagerResponse.data?.skipEmailScreen)` check
    - Log the entire configManagerResponse.data object (mask sensitive fields)

    In `authentication-view.tsx` useEffect (around line 456):
    - Add console.log to show what the API returned
    - Fix the navigation logic to not navigate if already on the correct route
    - Check for pathname before navigating to prevent unnecessary re-renders:

    ```typescript
    // Change line 474 from:
    navigate(`/auth/sign-in${suffix}`);

    // To:
    const currentPath = window.location.pathname;
    if (currentPath !== '/auth/sign-in') {
      navigate(`/auth/sign-in${suffix}`);
    }
    // Otherwise, stay on the page (email form will render)
    ```

    This prevents the redirect loop when user is already on /auth/sign-in.
  </action>
  <verify>
    - Backend compiles: `cd backend/nodejs/apps && npx tsc --noEmit`
    - Frontend compiles: `cd frontend && npx tsc --noEmit`
    - Test manually with browser devtools:
      1. Open Network tab, go to /auth/sign-in
      2. Check the response from GET /api/v1/auth/directSsoConfig
      3. Verify skipEmailScreen value matches what admin configured
  </verify>
  <done>
    - Debug logging added to identify the root cause
    - Frontend navigation logic fixed to prevent redirect when already on /auth/sign-in
    - Root cause documented in summary
  </done>
</task>

<task type="auto">
  <name>Task 2: Add instructions for admin to re-save config (if needed)</name>
  <files>
    frontend/src/sections/accountdetails/account-settings/auth/components/microsoft-auth-form.tsx
  </files>
  <action>
    If the diagnosis from Task 1 reveals that the config was saved before the skipEmailScreen field was added to the save function:

    **The solution is to inform the admin that they need to:**
    1. Go to Settings > Authentication > Microsoft
    2. Enable the "Skip Email Entry Screen" toggle
    3. Click Save to persist the new field

    **Optional enhancement (only if time permits):**
    Add a helper note in the Microsoft auth form that appears when skipEmailScreen is false:

    ```tsx
    {!formData.skipEmailScreen && (
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
        Enable this option and save to activate direct Microsoft SSO
      </Typography>
    )}
    ```

    **Skip this task if:**
    - Task 1 reveals the issue is NOT related to missing config data
    - The bug is purely in the frontend routing logic
  </action>
  <verify>
    - If changes made: Frontend compiles `cd frontend && npx tsc --noEmit`
    - If no changes needed: Document why in the summary
  </verify>
  <done>
    - Admin guidance documented or UX helper added
    - OR documented that this task was skipped because issue was elsewhere
  </done>
</task>

<task type="auto">
  <name>Task 3: Test the complete flow and document fix</name>
  <files>none - verification only</files>
  <action>
    **Test the end-to-end flow:**

    1. Start backend and frontend locally (if possible)
       - Or test against deployed environment with browser devtools

    2. As admin:
       - Go to Settings > Authentication > Microsoft
       - Verify "Skip Email Entry Screen" toggle exists and is OFF
       - Enable the toggle and click Save
       - Verify save succeeds (snackbar shows success)

    3. Test direct SSO flow:
       - Open a new incognito window
       - Navigate to /auth/sign-in (or base URL)
       - Check browser console for debug logs
       - Check Network tab for /api/v1/auth/directSsoConfig response
       - Verify `skipEmailScreen: true` and `microsoft: {...}` in response
       - User should see Microsoft SSO button directly (no email form)

    4. Test fallback flow:
       - Disable skipEmailScreen toggle in admin, save
       - Refresh incognito window
       - Verify user sees email form (not direct SSO)

    **Document findings:**
    - What was the root cause?
    - What was the fix?
    - Any data migration needed (e.g., admin must re-save)?
  </action>
  <verify>
    - Complete flow works: skipEmailScreen=true shows Microsoft button directly
    - Complete flow works: skipEmailScreen=false shows email form
    - No console errors
    - No infinite navigation loops
  </verify>
  <done>
    - Root cause identified and documented
    - Fix verified working
    - Both enabled and disabled states work correctly
  </done>
</task>

</tasks>

<verification>
- GET /api/v1/auth/directSsoConfig returns `{ skipEmailScreen: true, microsoft: {...} }` when enabled
- GET /api/v1/auth/directSsoConfig returns `{ skipEmailScreen: false }` when disabled
- Frontend renders Microsoft SSO button directly when skipEmailScreen is true
- Frontend renders email form when skipEmailScreen is false
- No console errors or navigation loops
</verification>

<success_criteria>
- Root cause identified (config not saved, routing bug, or other)
- Fix implemented and tested
- Skip email screen feature works as designed:
  - Enabled: Users go directly to Microsoft sign-in
  - Disabled: Users enter email first (existing flow)
</success_criteria>

<output>
After completion, create `.planning/quick/002-fix-skip-email-screen-not-working-for-di/002-SUMMARY.md`
</output>
