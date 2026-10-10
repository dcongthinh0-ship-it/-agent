let accessToken: string | null = null
export function setHostToken(value: string | null) { accessToken = value }
export function hasHostToken() { return Boolean(accessToken) }

export class ApiFailure extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message)
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { signal, headers: { Accept: 'application/json', ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) } })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiFailure(0, 'NETWORK_ERROR', '连接中断，请检查本地服务后重试')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiFailure(response.status, payload?.code ?? 'REQUEST_FAILED', payload?.message ?? '读取失败，请稍后重试')
  }
  return response.json() as Promise<T>
}

export async function postJson<T>(path: string, body: unknown, key: string = crypto.randomUUID(), signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { method: 'POST', signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': key, ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) }, body: JSON.stringify(body) })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiFailure(0, 'NETWORK_ERROR', '连接中断。请用本次操作继续重试，避免重复创建。')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiFailure(response.status, payload?.code ?? 'REQUEST_FAILED', payload?.message ?? '操作未完成，请重试')
  }
  return response.json() as Promise<T>
}
