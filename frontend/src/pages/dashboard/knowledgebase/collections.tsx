import { Helmet } from 'react-helmet-async';

import { CONFIG } from 'src/config-global';
import { UserProvider } from 'src/context/UserContext';

import Collections from 'src/sections/knowledgebase/collections';

// ----------------------------------------------------------------------

const metadata = { title: `Collections | Dashboard - ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <UserProvider>

      <Collections />
      </UserProvider>
    </>
  );
}
