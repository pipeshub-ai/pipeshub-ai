'use client';

import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Heading, Badge, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  useServicesHealthStore,
  selectAppServices,
  APP_SERVICE_LABELS,
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
  const { t, i18n } = useTranslation();
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
          {t('healthGate.serviceGate.title')}
        </Heading>

        {/* Service names help an admin act; to everyone else they are noise,
            and the status page they point at is admin-only. Unknown (profile
            still loading) counts as a member. */}
        {isAdmin === true ? (
          <Text size="2" style={{ color: 'var(--slate-10)', lineHeight: 1.6 }}>
            {t('healthGate.serviceGate.requires', {
              count: labels.length,
              services: new Intl.ListFormat(i18n.language, { type: 'conjunction' }).format(labels),
            })}
          </Text>
        ) : (
          <Text size="2" style={{ color: 'var(--slate-10)', lineHeight: 1.6 }}>
            {t('healthGate.serviceGate.memberBody')}
          </Text>
        )}

        {isAdmin === true && (
          <Flex gap="2" wrap="wrap" justify="center">
            {unhealthyServices.map((key) => (
              <Badge key={key} color="red" variant="soft" size="2">
                {APP_SERVICE_LABELS[key]}
              </Badge>
            ))}
          </Flex>
        )}

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
            {t('healthGate.serviceGate.viewStatus')}
          </Button>
        )}

      </Flex>
    </Flex>
  );
}
