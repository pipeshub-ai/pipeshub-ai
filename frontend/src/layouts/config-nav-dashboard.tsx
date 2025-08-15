import { paths } from 'src/routes/paths';

// Base navigation data that's common for all users
const baseNavData = [
  {
    subheader: 'Overview',
    items: [
      { title: 'Assistant', path: paths.dashboard.root },
      {
        title: 'Knowledge Base',
        path: paths.dashboard.knowledgebase.root,
      },
      {
        title: 'Knowledge Search',
        path: paths.dashboard.knowledgebase.search,
      },
    ],
  },
];

// Function to get navigation data based on user role
export const getDashboardNavData = (accountType: string | undefined, isAdmin: boolean) => {
  const isBusiness = accountType === 'business' || accountType === 'organization';
  
  const navigationData = [...baseNavData];
  
  if (isBusiness && isAdmin) {
    navigationData.push({
      subheader: 'Administration',
      items: [
        {
          title: 'Connector Settings',
          path: '/account/company-settings/settings/connector',
        },
      ],
    });
  } else if (!isBusiness) {
    navigationData.push({
      subheader: 'Settings',
      items: [
        {
          title: 'Connector Settings',
          path: '/account/individual/settings/connector',
        },
      ],
    });
  }
  
  return navigationData;
};

// Default export for backward compatibility
export const navData = baseNavData;
