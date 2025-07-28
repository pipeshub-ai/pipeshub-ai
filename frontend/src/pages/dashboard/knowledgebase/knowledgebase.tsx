import { Helmet } from 'react-helmet-async';

import { CONFIG } from 'src/config-global';
import { UserProvider } from 'src/context/UserContext';

import KnowledgeBase from 'src/sections/knowledgebase/knowledge-base';

// ----------------------------------------------------------------------

const metadata = { title: `Knowledge Base | Dashboard - ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <UserProvider>

      <KnowledgeBase />
      </UserProvider>
    </>
  );
}
