import { getJson } from './client'
import type { CapabilityStatus } from '../types/contracts'

export function getCapabilities(signal?: AbortSignal) {
  return getJson<CapabilityStatus>('/api/v1/status', signal)
}
