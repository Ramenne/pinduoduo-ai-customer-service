import { get } from '@/utils/request'

export const fetchPlatforms = () => get('/platforms')
export const fetchPlatformAccounts = (platform, params = {}) =>
  get(`/platforms/${encodeURIComponent(platform)}/accounts`, params)
