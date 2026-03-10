import { paths } from 'src/routes/paths';

// Base navigation data that's common for all users
const baseNavData = [
  {
    subheader: 'Overview',
    items: [
      { title: 'Assistant', path: paths.dashboard.root },
      {
        title: 'Agent (Beta)',
        path: paths.dashboard.agent.root,
      },
      {
        title: 'All Records',
        path: paths.dashboard.allRecords,
      },
      {
        title: 'Collections',
        path: paths.dashboard.collections.root,
      },
      {
        title: 'Knowledge Search',
        path: paths.dashboard.knowledgeSearch.root,
      },
    ],
  },
];

// Function to get navigation data based on user role
export const getDashboardNavData = (accountType: string | undefined, isAdmin: boolean) => {
  const isBusiness = accountType === 'business' || accountType === 'organization';
  
  const navigationData = [...baseNavData];
  
  if (isBusiness) {
    // Admins default to team scope, non-admins to personal scope
    const scope = isAdmin ? 'team' : 'personal';
    navigationData.push({
      subheader: 'Administration',
      items: [
        {
          title: 'Connector Settings',
          path: `/account/company-settings/settings/connector?scope=${scope}`,
        },
      ],
    });
  } else if (!isBusiness) {
    navigationData.push({
      subheader: 'Settings',
      items: [
        {
          title: 'Connector Settings',
          path: '/account/individual/settings/connector?scope=team',
        },
      ],
    });
  }
  
  return navigationData;
};

// Default export for backward compatibility
export const navData = baseNavData;
