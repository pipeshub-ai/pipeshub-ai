import { Helmet } from 'react-helmet-async';

import { OAuthAuthorizeView } from 'src/sections/oauth/oauth-authorize-view';

// ----------------------------------------------------------------------

const metadata = { title: 'Authorize Application' };

export default function OAuthAuthorizePage() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>
      <OAuthAuthorizeView />
    </>
  );
}
