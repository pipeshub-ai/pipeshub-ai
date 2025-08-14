import { Helmet } from 'react-helmet-async';
import { useParams, useLocation } from 'react-router-dom';

import { UserProvider } from 'src/context/UserContext';
import { AuthProvider } from 'src/auth/context/jwt';

import { AgentsManagement, AgentChat } from 'src/sections/qna/agents';

// ----------------------------------------------------------------------

const metadata = { title: `Agent` };

export default function Page() {
  const { agentKey, conversationKey } = useParams<{ agentKey: string; conversationKey?: string }>();
  const location = useLocation();

  // Determine which component to render based on the route
  const renderComponent = () => {
    if (agentKey) {
      // Agent chat route
      return <AgentChat />;
    }
    // Default agents management route
    return <AgentsManagement />;
  };

  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <AuthProvider>
        <UserProvider>
          {renderComponent()}
        </UserProvider>
      </AuthProvider>
    </>
  );
}
