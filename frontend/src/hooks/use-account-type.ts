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

// Module-level cache to prevent duplicate API calls
let cachedOrgData: OrgData | null = null;
let cacheTimestamp: number = 0;
let fetchPromise: Promise<OrgData> | null = null;
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

export function useAccountType() {
  const [orgData, setOrgData] = useState<OrgData | null>(cachedOrgData);
  const [loading, setLoading] = useState(!cachedOrgData);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Return cached data if still valid
    if (cachedOrgData && Date.now() - cacheTimestamp < CACHE_DURATION) {
      setOrgData(cachedOrgData);
      setLoading(false);
      return;
    }

    // If a fetch is already in progress, wait for it
    if (fetchPromise) {
      fetchPromise
        .then((data) => {
          setOrgData(data);
          setLoading(false);
        })
        .catch((err) => {
          console.error('Error fetching organization data:', err);
          setError('Failed to fetch organization data');
          setLoading(false);
        });
      return;
    }

    // Start new fetch
    const fetchOrgData = async (): Promise<OrgData> => {
      try {
        setLoading(true);
        setError(null);
        const response = await axios.get('/api/v1/org');
        const data = response.data;
        
        // Update cache
        cachedOrgData = data;
        cacheTimestamp = Date.now();
        fetchPromise = null;
        
        setOrgData(data);
        return data;
      } catch (err) {
        console.error('Error fetching organization data:', err);
        setError('Failed to fetch organization data');
        fetchPromise = null;
        throw err;
      } finally {
        setLoading(false);
      }
    };

    // Store the promise to share across multiple hook instances
    fetchPromise = fetchOrgData();
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
