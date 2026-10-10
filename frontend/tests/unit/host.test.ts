import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

test('duplicate host context on widget reopen does not remount the editor', async () => {
  const listeners: Record<string, (value: any) => void> = {}
  const received: string[] = []
  const parent = { postMessage() {} }
  const window = { parent, addEventListener: (kind: string, fn: any) => { listeners[kind] = fn }, removeEventListener() {} }
  const source = readFileSync(new URL('../../src/integrations/host.ts', import.meta.url), 'utf8')
    .replace(/^import.*\n/, '').replace('export async function', 'async function')
  const { stripTypeScriptTypes } = await import('node:module')
  const code = stripTypeScriptTypes(source)
  const scope: any = { window, getJson: async () => ({ trusted_origins: ['http://trusted.local'] }), setHostToken() {} }
  runInNewContext(code + '\nthis.connectHost = connectHost;', scope)
  await scope.connectHost((id: string) => received.push(id))
  const event = { source: parent, origin: 'http://trusted.local', data: { type: 'CHEMO_CONTEXT', version: '1', context_id: '12345678-1234-1234-1234-123456789abc', access_token: 'test-only-token' } }
  listeners.message(event); listeners.message(event)
  assert.equal(received.length, 1)
  listeners.message({ ...event, origin: 'http://untrusted.local' })
  assert.equal(received.length, 1)
  listeners.message({ ...event, data: { ...event.data, access_token: 'renewed-token' } })
  assert.equal(received.length, 2)
})
