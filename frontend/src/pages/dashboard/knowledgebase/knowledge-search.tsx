import { Helmet } from 'react-helmet-async';

import { CONFIG } from 'src/config-global';

import KnowledgeBaseSearch from 'src/sections/knowledgebase/knowledge-base-search';

// ----------------------------------------------------------------------

const metadata = { title: `Knowledge Search | ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>

      <KnowledgeBaseSearch />
    </>
  );
}
