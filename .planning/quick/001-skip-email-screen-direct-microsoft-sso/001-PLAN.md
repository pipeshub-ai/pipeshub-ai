---
phase: quick
plan: 001
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/nodejs/apps/src/modules/configuration_manager/validator/validators.ts
  - backend/nodejs/apps/src/modules/configuration_manager/controller/cm_controller.ts
  - frontend/src/sections/accountdetails/account-settings/auth/utils/auth-configuration-service.ts
  - frontend/src/sections/accountdetails/account-settings/auth/components/microsoft-auth-form.tsx
  - frontend/src/auth/view/auth/authentication-view.tsx
  - backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
autonomous: true

must_haves:
  truths:
    - "Admin can enable 'Skip Email Screen' toggle in Microsoft auth settings"
    - "When skipEmailScreen is enabled, users go directly to Microsoft SSO without entering email"
    - "When skipEmailScreen is disabled, existing email-first flow continues working"
    - "JIT provisioning still works with direct Microsoft SSO flow"
  artifacts:
    - path: "backend/nodejs/apps/src/modules/configuration_manager/validator/validators.ts"
      provides: "skipEmailScreen field validation"
      contains: "skipEmailScreen"
    - path: "frontend/src/sections/accountdetails/account-settings/auth/components/microsoft-auth-form.tsx"
      provides: "Skip Email Screen toggle UI"
      contains: "skipEmailScreen"
    - path: "frontend/src/auth/view/auth/authentication-view.tsx"
      provides: "Direct SSO flow logic"
      contains: "skipEmailScreen"
  key_links:
    - from: "microsoft-auth-form.tsx"
      to: "cm_controller.ts"
      via: "POST /api/v1/configurationManager/authConfig/microsoft"
      pattern: "skipEmailScreen"
    - from: "authentication-view.tsx"
      to: "/api/v1/auth/initAuth"
      via: "authInitConfig API"
      pattern: "skipEmailScreen.*microsoft"
---

<objective>
Add a `skipEmailScreen` configuration option to Microsoft SSO that allows organizations to bypass the email entry screen and go directly to Microsoft login.

Purpose: Eliminate inconsistent UX where users enter email on screen 1 but may use a different email in Microsoft SSO on screen 2. Direct SSO flow ensures single source of truth for user identity.

Output:
- New toggle in Microsoft auth admin settings
- Modified login flow that checks for skipEmailScreen and shows Microsoft SSO directly
- Backward compatible - existing email-first flow unchanged when toggle is off
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/STATE.md
@backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
@backend/nodejs/apps/src/modules/configuration_manager/controller/cm_controller.ts
@frontend/src/auth/view/auth/authentication-view.tsx
@frontend/src/sections/accountdetails/account-settings/auth/components/microsoft-auth-form.tsx
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add skipEmailScreen to backend config schema and storage</name>
  <files>
    backend/nodejs/apps/src/modules/configuration_manager/validator/validators.ts
    backend/nodejs/apps/src/modules/configuration_manager/controller/cm_controller.ts
    frontend/src/sections/accountdetails/account-settings/auth/utils/auth-configuration-service.ts
  </files>
  <action>
    1. In validators.ts, add `skipEmailScreen: z.boolean().optional().default(false)` to microsoftConfigSchema (around line 127-131)

    2. In cm_controller.ts setMicrosoftAuthConfig function (around line 546-584):
       - Extract skipEmailScreen from req.body alongside clientId, tenantId, enableJit
       - Include skipEmailScreen in the encrypted JSON: `{ clientId, tenantId, authority, enableJit, skipEmailScreen: skipEmailScreen ?? false }`

    3. In auth-configuration-service.ts, add `skipEmailScreen?: boolean` to the MicrosoftAuthConfig interface (around line 15-19)
  </action>
  <verify>
    - TypeScript compilation passes: `cd backend/nodejs/apps && npx tsc --noEmit`
    - Frontend compilation passes: `cd frontend && npm run build`
  </verify>
  <done>
    - skipEmailScreen field added to validator schema with default false
    - setMicrosoftAuthConfig stores skipEmailScreen in encrypted config
    - MicrosoftAuthConfig TypeScript interface includes skipEmailScreen
  </done>
</task>

<task type="auto">
  <name>Task 2: Add skipEmailScreen toggle to Microsoft auth admin form</name>
  <files>
    frontend/src/sections/accountdetails/account-settings/auth/components/microsoft-auth-form.tsx
  </files>
  <action>
    1. Add skipEmailScreen to formData state (default false):
       ```typescript
       const [formData, setFormData] = useState({
         clientId: '',
         tenantId: '',
         redirectUri: ...,
         enableJit: true,
         skipEmailScreen: false,  // Add this
       });
       ```

    2. In useEffect fetchConfig, load skipEmailScreen from config:
       ```typescript
       setFormData({
         ...existing fields,
         skipEmailScreen: config?.skipEmailScreen ?? false,
       });
       ```

    3. In handleSave, include skipEmailScreen in the update call:
       ```typescript
       await updateMicrosoftAuthConfig({
         clientId: formData.clientId,
         tenantId: formData.tenantId,
         enableJit: formData.enableJit,
         skipEmailScreen: formData.skipEmailScreen,
       });
       ```

    4. Add UI toggle after the JIT Provisioning toggle (similar styling). Use this structure:
       ```tsx
       <Grid item xs={12}>
         <Box sx={{ p: 2, borderRadius: 1, bgcolor: alpha(theme.palette.warning.main, 0.04), border: `1px solid ${alpha(theme.palette.warning.main, 0.15)}` }}>
           <FormControlLabel
             control={
               <Switch
                 checked={formData.skipEmailScreen}
                 onChange={(e) => setFormData((prev) => ({ ...prev, skipEmailScreen: e.target.checked }))}
                 color="warning"
               />
             }
             label={
               <Box>
                 <Typography variant="subtitle2">Skip Email Entry Screen</Typography>
                 <Typography variant="caption" color="text.secondary">
                   Users will be taken directly to Microsoft sign-in without entering their email first.
                   Recommended for organizations using Microsoft as the only sign-in method.
                 </Typography>
               </Box>
             }
             sx={{ alignItems: 'flex-start', ml: 0 }}
           />
         </Box>
       </Grid>
       ```
  </action>
  <verify>
    - Frontend compiles: `cd frontend && npm run build`
    - Manual: Navigate to Account Settings > Authentication > Microsoft, see new toggle
  </verify>
  <done>
    - Skip Email Screen toggle visible in Microsoft auth settings
    - Toggle state persists across page reloads
    - Toggle saves to backend when clicking Save
  </done>
</task>

<task type="auto">
  <name>Task 3: Implement direct Microsoft SSO flow in authentication view</name>
  <files>
    frontend/src/auth/view/auth/authentication-view.tsx
    backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
  </files>
  <action>
    1. In userAccount.controller.ts initAuth method (around line 204-459):
       - When loading Microsoft config for JIT provisioning (around line 278-293), include skipEmailScreen in authProviders.microsoft:
         ```typescript
         if (configManagerResponse.data?.enableJit) {
           jitEnabledMethods.push('microsoft');
           jitConfig.microsoft = true;
           authProviders.microsoft = configManagerResponse.data; // This already includes all fields
         }
         ```
       - Ensure the response JSON includes a top-level indicator when skipEmailScreen is true:
         At the end of initAuth (around line 365-372), add:
         ```typescript
         res.json({
           currentStep: 0,
           allowedMethods: jitEnabledMethods.length > 0 ? jitEnabledMethods : ['password'],
           message: 'Authentication initialized',
           authProviders,
           jitEnabled: jitEnabledMethods.length > 0,
           skipEmailScreen: authProviders?.microsoft?.skipEmailScreen ?? false, // Add this
         });
         ```
       - Also add this to the existing user flow response (around line 450-455):
         ```typescript
         res.json({
           currentStep: 0,
           allowedMethods,
           message: 'Authentication initialized',
           authProviders,
           skipEmailScreen: authProviders?.microsoft?.skipEmailScreen ?? false, // Add this
         });
         ```

    2. In authentication-view.tsx:
       - Add a new state to track direct SSO mode:
         ```typescript
         const [directMicrosoftSso, setDirectMicrosoftSso] = useState(false);
         const [microsoftConfig, setMicrosoftConfig] = useState<any>(null);
         ```

       - Create a new API function to check for skipEmailScreen config (add before AuthenticationView component):
         ```typescript
         const checkDirectSsoConfig = async () => {
           try {
             // Call initAuth with empty email to get org-level SSO config
             const response = await axios.get('/api/v1/auth/directSsoConfig');
             return response.data;
           } catch (error) {
             return null;
           }
         };
         ```

       - Actually, simpler approach: Add a new backend endpoint or modify OrgExists check.
         Better: In the useEffect that calls OrgExists (around line 453-473), also fetch direct SSO config:
         ```typescript
         useEffect(() => {
           const checkOrgAndSsoConfig = async () => {
             try {
               const [orgResponse, ssoConfigResponse] = await Promise.all([
                 OrgExists(),
                 axios.get('/api/v1/auth/directSsoConfig').catch(() => ({ data: null })),
               ]);

               // Preserve query params when navigating
               const queryString = searchParams.toString();
               const suffix = queryString ? `?${queryString}` : '';

               if (orgResponse.exists === false) {
                 navigate(`/auth/sign-up${suffix}`);
               } else if (ssoConfigResponse.data?.skipEmailScreen && ssoConfigResponse.data?.microsoft) {
                 // Direct SSO mode - skip email screen
                 setDirectMicrosoftSso(true);
                 setMicrosoftConfig(ssoConfigResponse.data.microsoft);
               } else {
                 navigate(`/auth/sign-in${suffix}`);
               }
             } catch (err) {
               console.error('Error checking org/SSO config:', err);
             }
           };

           checkOrgAndSsoConfig();
         }, []);
         ```

       - Add conditional rendering for direct SSO mode. Before the email form (around line 476), add:
         ```tsx
         // Direct Microsoft SSO mode
         if (directMicrosoftSso && microsoftConfig) {
           return (
             <Fade in timeout={450}>
               <Card sx={{ width: '100%', maxWidth: 480, mx: 'auto', mt: 4, ... }}>
                 <CardContent sx={{ pt: 5, pb: 5 }}>
                   <Box sx={{ mb: 5, textAlign: 'center' }}>
                     <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>Welcome</Typography>
                     <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                       Sign in with your organization account
                     </Typography>
                   </Box>
                   {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 3 }}>{error}</Alert>}
                   <MicrosoftLoginButton
                     config={socialConfig.microsoft}
                     method="microsoft"
                     clientId={microsoftConfig.clientId}
                     authority={microsoftConfig.authority || `https://login.microsoftonline.com/${microsoftConfig.tenantId}`}
                     onSuccess={handleMsalLoginSuccess}
                     onError={(errorMessage) => setError(errorMessage)}
                   />
                 </CardContent>
               </Card>
             </Fade>
           );
         }
         ```

    3. Add backend endpoint for directSsoConfig in userAccount.controller.ts:
       Add a new method after initAuth:
       ```typescript
       async getDirectSsoConfig(
         req: Request,
         res: Response,
         next: NextFunction,
       ): Promise<void> {
         try {
           // Find any org with skipEmailScreen enabled
           const orgAuthConfig = await OrgAuthConfig.findOne({
             isDeleted: false,
             'authSteps.allowedMethods.type': 'microsoft',
           });

           if (!orgAuthConfig) {
             res.json({ skipEmailScreen: false });
             return;
           }

           // Get Microsoft config
           const configManagerResponse = await this.configurationManagerService.getConfig(
             this.config.cmBackend,
             MICROSOFT_AUTH_CONFIG_PATH,
             { orgId: orgAuthConfig.orgId },
             this.config.scopedJwtSecret,
           );

           if (configManagerResponse.data?.skipEmailScreen) {
             res.json({
               skipEmailScreen: true,
               microsoft: {
                 clientId: configManagerResponse.data.clientId,
                 tenantId: configManagerResponse.data.tenantId,
                 authority: configManagerResponse.data.authority,
               },
             });
           } else {
             res.json({ skipEmailScreen: false });
           }
         } catch (error) {
           next(error);
         }
       }
       ```

    4. Add route for directSsoConfig in userAccount.routes.ts:
       ```typescript
       router.get('/directSsoConfig', userAccountController.getDirectSsoConfig.bind(userAccountController));
       ```

    5. Add import for axios in authentication-view.tsx if not present, and add the API call for directSsoConfig.
  </action>
  <verify>
    - Backend compiles: `cd backend/nodejs/apps && npx tsc --noEmit`
    - Frontend compiles: `cd frontend && npm run build`
    - Manual testing:
      1. With skipEmailScreen OFF: User sees email form first, then auth options
      2. With skipEmailScreen ON: User sees Microsoft SSO button directly (no email form)
      3. JIT provisioning works in both modes
  </verify>
  <done>
    - New GET /api/v1/auth/directSsoConfig endpoint returns skipEmailScreen status and Microsoft config
    - Frontend checks directSsoConfig on mount
    - When skipEmailScreen is true, renders Microsoft SSO button directly without email form
    - Existing email-first flow unchanged when skipEmailScreen is false
  </done>
</task>

</tasks>

<verification>
1. Backend TypeScript compilation: `cd backend/nodejs/apps && npx tsc --noEmit`
2. Frontend build: `cd frontend && npm run build`
3. Functional testing:
   - Enable skipEmailScreen toggle in admin settings
   - Open incognito window, navigate to login
   - Should see Microsoft SSO button directly without email form
   - Click Microsoft SSO, authenticate, user created/logged in
   - Disable skipEmailScreen toggle
   - Open new incognito window
   - Should see email form first (existing flow)
</verification>

<success_criteria>
- [ ] skipEmailScreen field persists in Microsoft auth configuration
- [ ] Toggle visible and functional in admin Microsoft auth settings
- [ ] When enabled, login page shows Microsoft SSO directly
- [ ] When disabled, login page shows email form first (no regression)
- [ ] JIT provisioning works with direct SSO flow
- [ ] TypeScript compilation passes for both backend and frontend
</success_criteria>

<output>
After completion, create `.planning/quick/001-skip-email-screen-direct-microsoft-sso/001-SUMMARY.md`
</output>
