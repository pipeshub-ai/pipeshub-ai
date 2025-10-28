import type { Theme, SxProps, Breakpoint } from '@mui/material/styles';

import { Section } from './section';
import { Main, Content } from './main';
import { LayoutSection } from '../core/layout-section';

// ----------------------------------------------------------------------

export type AuthSplitLayoutProps = {
  sx?: SxProps<Theme>;
  children: React.ReactNode;
  section?: {
    title?: string;
    imgUrl?: string;
    subtitle?: string;
    videoUrl?: string;
  };
};

export function AuthSplitLayout({ sx, section, children }: AuthSplitLayoutProps) {
  const layoutQuery: Breakpoint = 'md';

  return (
    <LayoutSection
      headerSection={null} // Removed header
      footerSection={null}
      cssVars={{ '--layout-auth-content-width': '100%' }}
      sx={sx}
    >
      <Main layoutQuery={layoutQuery}>
        {/* Section for illustration/info */}
        <Section
          layoutQuery={layoutQuery}
          title={section?.title}
          subtitle={section?.subtitle}
          imgUrl={section?.imgUrl}
          videoUrl={section?.videoUrl}
        />

        {/* Content area (form) */}
        <Content layoutQuery={layoutQuery}>
          {children}
        </Content>
      </Main>
    </LayoutSection>
  );
}
