import { Helmet } from 'react-helmet-async';

import { CONFIG } from 'src/config-global';
import { EmailChangeRedirect } from 'src/sections/accountdetails/account-settings/email-change-redirect';
// ----------------------------------------------------------------------

const metadata = { title: `Reset Email | ${CONFIG.appName}` };

export default function Page() {
    return (
        <>
            <Helmet>
                <title> {metadata.title}</title>
            </Helmet>

            <EmailChangeRedirect />
        </>
    );
}
