import { createRoot } from 'react-dom/client';
import { Theme } from '@radix-ui/themes';
import '@radix-ui/themes/styles.css';
import '@/app/globals.css';
import { GraphExplorer } from '@/app/(main)/graph/components/graph-explorer';
import './fake-graph-server';

createRoot(document.getElementById('root')!).render(
  <Theme accentColor="jade" grayColor="olive" radius="medium">
    <div style={{ height: '100%' }}>
      <GraphExplorer />
    </div>
  </Theme>,
);
