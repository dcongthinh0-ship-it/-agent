import { getJson, setHostToken } from './api'

export async function connectHost(onContext: (id: string | null) => void): Promise<() => void> {
  const config = await getJson<{ trusted_origins: string[] }>('/api/v1/host-contract').catch((): { trusted_origins: string[] } => ({ trusted_origins: [] }))
  const listener = (event: MessageEvent) => {
    if (event.source !== window.parent || !config.trusted_origins.includes(event.origin)) return
    const data = event.data
    if (data?.type !== 'CHEMO_CONTEXT' || data?.version !== '1' || typeof data.context_id !== 'string' || typeof data.access_token !== 'string') return
    if (!/^[0-9a-f-]{36}$/i.test(data.context_id) || data.access_token.length > 8192) return
    setHostToken(data.access_token)
    onContext(data.context_id)
  }
  window.addEventListener('message', listener)
  if (window.parent !== window) {
    for (const origin of config.trusted_origins) window.parent.postMessage({ type: 'CHEMO_READY', version: '1' }, origin)
  }
  return () => { window.removeEventListener('message', listener); setHostToken(null) }
}
