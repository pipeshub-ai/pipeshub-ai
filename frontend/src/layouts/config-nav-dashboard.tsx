import { paths } from 'src/routes/paths';

// Base navigation data that's common for all users
const baseNavData = [
  {
    subheader: 'Overview',
    items: [
      { title: 'Assistant', path: paths.dashboard.root },
    ],
  },
];

// Function to get navigation data based on user role
export const getDashboardNavData = (_accountType: string | undefined, _isAdmin: boolean) =>
  baseNavData.map((section) => ({
    ...section,
    items: [...section.items],
  }));

// Default export for backward compatibility
export const navData = baseNavData;
