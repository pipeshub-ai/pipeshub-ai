import gavelIcon from '@iconify-icons/mdi/gavel';
import codeIcon from '@iconify-icons/mdi/code-tags';
import securityIcon from '@iconify-icons/mdi/security';
import headsetMicIcon from '@iconify-icons/mdi/headset';
import settingsIcon from '@iconify-icons/mdi/cog-outline';
import engineeringIcon from '@iconify-icons/mdi/encryption';
import trendingUpIcon from '@iconify-icons/mdi/trending-up';
import campaignIcon from '@iconify-icons/mdi/bullhorn-outline';
import accountBalanceIcon from '@iconify-icons/mdi/bank-outline';
import analyticsOutlineIcon from '@iconify-icons/mdi/chart-line';
// Icon imports - Add these at the top of your file
import folderOutlineIcon from '@iconify-icons/mdi/folder-outline';
import schoolOutlineIcon from '@iconify-icons/mdi/school-outline';
import autoStoriesIcon from '@iconify-icons/mdi/book-open-outline';
import paletteOutlineIcon from '@iconify-icons/mdi/palette-outline';
import inventoryIcon from '@iconify-icons/mdi/package-variant-closed';
import peopleOutlineIcon from '@iconify-icons/mdi/account-group-outline';
import descriptionOutlineIcon from '@iconify-icons/mdi/file-document-outline';

export const getKBIcon = (name: string) => {
  const lowerName = name.toLowerCase();

  if (
    lowerName.includes('hr') ||
    lowerName.includes('human') ||
    lowerName.includes('people') ||
    lowerName.includes('employee')
  ) {
    return peopleOutlineIcon;
  }
  if (
    lowerName.includes('engineering') ||
    lowerName.includes('tech') ||
    lowerName.includes('development') ||
    lowerName.includes('software')
  ) {
    return engineeringIcon;
  }
  if (
    lowerName.includes('sales') ||
    lowerName.includes('revenue') ||
    lowerName.includes('deals') ||
    lowerName.includes('crm')
  ) {
    return trendingUpIcon;
  }
  if (
    lowerName.includes('marketing') ||
    lowerName.includes('campaign') ||
    lowerName.includes('brand') ||
    lowerName.includes('promotion')
  ) {
    return campaignIcon;
  }
  if (
    lowerName.includes('finance') ||
    lowerName.includes('accounting') ||
    lowerName.includes('budget') ||
    lowerName.includes('expense')
  ) {
    return accountBalanceIcon;
  }
  if (
    lowerName.includes('legal') ||
    lowerName.includes('compliance') ||
    lowerName.includes('policy') ||
    lowerName.includes('contract')
  ) {
    return gavelIcon;
  }
  if (
    lowerName.includes('operations') ||
    lowerName.includes('ops') ||
    lowerName.includes('logistics') ||
    lowerName.includes('supply')
  ) {
    return settingsIcon;
  }
  if (
    lowerName.includes('customer') ||
    lowerName.includes('support') ||
    lowerName.includes('service') ||
    lowerName.includes('help')
  ) {
    return headsetMicIcon;
  }
  if (
    lowerName.includes('product') ||
    lowerName.includes('feature') ||
    lowerName.includes('roadmap')
  ) {
    return inventoryIcon;
  }
  if (
    lowerName.includes('design') ||
    lowerName.includes('ui') ||
    lowerName.includes('ux') ||
    lowerName.includes('creative')
  ) {
    return paletteOutlineIcon;
  }
  if (
    lowerName.includes('security') ||
    lowerName.includes('cyber') ||
    lowerName.includes('privacy') ||
    lowerName.includes('audit')
  ) {
    return securityIcon;
  }

  // Content-type specific icons
  if (
    lowerName.includes('doc') ||
    lowerName.includes('guide') ||
    lowerName.includes('manual') ||
    lowerName.includes('handbook')
  ) {
    return descriptionOutlineIcon;
  }
  if (
    lowerName.includes('api') ||
    lowerName.includes('code') ||
    lowerName.includes('dev') ||
    lowerName.includes('sdk')
  ) {
    return codeIcon;
  }
  if (
    lowerName.includes('data') ||
    lowerName.includes('analytics') ||
    lowerName.includes('report') ||
    lowerName.includes('metrics')
  ) {
    return analyticsOutlineIcon;
  }
  if (
    lowerName.includes('training') ||
    lowerName.includes('learn') ||
    lowerName.includes('course') ||
    lowerName.includes('onboard')
  ) {
    return schoolOutlineIcon;
  }
  if (lowerName.includes('wiki') || lowerName.includes('knowledge') || lowerName.includes('faq')) {
    return autoStoriesIcon;
  }

  return folderOutlineIcon; // Default: simple folder icon
};
