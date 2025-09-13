import { useState, useEffect } from 'react';
import axios from 'src/utils/axios';

interface OrgData {
  _id: string;
  registeredName: string;
  shortName: string;
  domain: string;
  contactEmail: string;
  accountType: 'business' | 'individual' | 'organization';
  permanentAddress: {
    addressLine1: string;
    city: string;
    state: string;
    postCode: string;
    country: string;
    _id: string;
  };
  onBoardingStatus: string;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug: string;
  __v: number;
}

export function useAccountType() {
  const [orgData, setOrgData] = useState<OrgData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchOrgData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await axios.get('/api/v1/org');
        setOrgData(response.data);
      } catch (err) {
        console.error('Error fetching organization data:', err);
        setError('Failed to fetch organization data');
      } finally {
        setLoading(false);
      }
    };

    fetchOrgData();
  }, []);

  const isBusiness = orgData?.accountType === 'business' || orgData?.accountType === 'organization';
  const isIndividual = !isBusiness;
  
  return {
    accountType: isBusiness ? 'business' : 'individual',
    isBusiness,
    isIndividual,
    orgData,
    loading,
    error
  };
}
