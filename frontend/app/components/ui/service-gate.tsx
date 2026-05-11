'use client';

import { useRouter } from 'next/navigation';
import { Flex, Text, Heading, Badge, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  useServicesHealthStore,
  selectAppServices,
  APP_SERVICE_LABELS,
  formatServiceList,
  type AppServices,
} from '@/lib/store/services-health-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

type AppServiceKey = keyof AppServices;

interface ServiceGateProps {
  children: React.ReactNode;
  services: AppServiceKey[];
}

export type { AppServiceKey };

export function ServiceGate({ children, services }: ServiceGateProps) {
  const router = useRouter();
  const appServices = useServicesHealthStore(selectAppServices);
  const isAdmin = useUserStore(selectIsAdmin);

  if (!appServices) {
    return <>{children}</>;
  }

  const unhealthyServices = services.filter(
    (key) => appServices[key] === 'unhealthy',
  );

  if (unhealthyServices.length === 0) {
    return <>{children}</>;
  }

  const labels = unhealthyServices.map((k) => APP_SERVICE_LABELS[k]);

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        height: '100%',
        width: '100%',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <Flex
        direction="column"
        align="center"
        gap="4"
        style={{ maxWidth: 420, textAlign: 'center', padding: '0 24px' }}
      >
        <Flex
          align="center"
          justify="center"
          style={{
            width: 64,
            height: 64,
            borderRadius: 'var(--radius-3)',
            backgroundColor: 'var(--red-a2)',
          }}
        >
          <MaterialIcon name="cloud_off" size={32} color="var(--red-9)" />
        </Flex>

        <Heading size="5" style={{ color: 'var(--slate-12)' }}>
          Service Unavailable
        </Heading>

        <Text size="2" style={{ color: 'var(--slate-10)', lineHeight: 1.6 }}>
          This page requires {formatServiceList(labels)} which{' '}
          {unhealthyServices.length === 1 ? 'is' : 'are'} currently
          unavailable. It will become available automatically once{' '}
          {unhealthyServices.length === 1
            ? 'the service recovers'
            : 'the services recover'}
          .
        </Text>

        <Flex gap="2" wrap="wrap" justify="center">
          {unhealthyServices.map((key) => (
            <Badge key={key} color="red" variant="soft" size="2">
              {APP_SERVICE_LABELS[key]}
            </Badge>
          ))}
        </Flex>

        {isAdmin === true && (
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() => router.push('/workspace/services/')}
            style={{ marginTop: 8, cursor: 'pointer', gap: 6 }}
          >
            <span className="material-icons-outlined" style={{ fontSize: 16 }}>
              monitor_heart
            </span>
            View Service Status
          </Button>
        )}
        {isAdmin === false && (
          <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 8 }}>
            Please contact your administrator for assistance.
          </Text>
        )}
      </Flex>
    </Flex>
  );
}
