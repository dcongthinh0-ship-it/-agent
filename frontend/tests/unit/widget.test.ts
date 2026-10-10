import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { webcrypto } from 'node:crypto'

function setup(getAccessToken: (payload: Record<string, unknown>) => Promise<string>) {
  const nodes: any[] = [], events: Record<string, (event: unknown) => void> = {}, calls: any[] = [], messages: any[] = []
  const element = (tag: string) => { const node: any = { tag, style: {}, children: [], hidden: false, src: '', attributes: {}, contentWindow: { postMessage: (message: unknown, origin: string) => messages.push({ message, origin }) }, append(...children: any[]) { this.children.push(...children) }, setAttribute(key: string, value: string) { this.attributes[key] = value }, removeAttribute(key: string) { delete this.attributes[key]; if (key === 'src') this.src = '' }, remove() {}, focus() {} }; nodes.push(node); return node }
  const timers = new Map<number, () => void>(); let timerId = 0
  const window: any = { addEventListener: (name: string, callback: (event: unknown) => void) => { events[name] = callback }, removeEventListener: (name: string) => { delete events[name] } }
  runInNewContext(readFileSync(new URL('../../public/chemo-widget.js', import.meta.url), 'utf8'), { window, document: { createElement: element, body: { append() {} } }, crypto: webcrypto, URL, AbortController, Date, setTimeout: (callback: () => void) => { timers.set(++timerId, callback); return timerId }, clearTimeout: (id: number) => timers.delete(id), fetch: async (url: string, options: any) => {
    calls.push({ url, options });
    const value = url.endsWith('/launch-context') ? { context_id: JSON.parse(options.body).patient_id } : { context_state: 'ACTIVE', expires_at: '2040-01-01T00:00:00Z', prepare_status: 'SUCCEEDED', decision_status: 'SUCCEEDED', candidate_count: 2 }
    return { ok: true, json: async () => value }
  } })
  return { widget: window.createChemoWidget({ serviceOrigin: 'http://127.0.0.1:5175', getAccessToken }), nodes, calls, messages, events, timers }
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
const patient = (id: string) => ({ patient_id: id, encounter_id: `ENC_${id}`, operator_id: 'CONTRACT_DOCTOR' })

test('patient change prepares while closed, readiness is real and opening does not relaunch', async () => {
  const t = setup(async () => 'CONTRACT_TOKEN')
  await t.widget.updatePatient(patient('CONTRACT_A')); await flush()
  const frame = t.nodes.find(node => node.tag === 'iframe'), panel = t.nodes.find(node => node.tag === 'section'), button = t.nodes.find(node => node.tag === 'button')
  assert.equal(panel.hidden, true)
  assert.equal(frame.src, '')
  assert.equal(t.calls.filter(call => call.url.endsWith('/launch-context')).length, 1)
  const launch = JSON.parse(t.calls[0].options.body)
  assert.equal(launch.request_scene, 'AUTO_PREPARE')
  assert.equal(launch.client_generation, 1)
  assert.equal(button.textContent, '化疗智能体 · 候选已就绪')
  button.onclick(); await flush()
  assert.equal(panel.hidden, false)
  assert.match(frame.src, /context_id=CONTRACT_A/)
  assert.equal(t.calls.filter(call => call.url.endsWith('/launch-context')).length, 1)
  assert.equal(frame.src.includes('TOKEN'), false)
  t.events.message({ origin: 'http://untrusted.invalid', source: frame.contentWindow, data: { type: 'CHEMO_READY' } })
  assert.equal(t.messages.length, 0) // no token is sent to an unloaded or untrusted iframe
  t.widget.destroy(); assert.equal(t.timers.size, 0)
})

test('slow old patient credentials never overwrite new patient token or response', async () => {
  let release: (value: string) => void = () => {}
  const t = setup(payload => payload.patient_id === 'CONTRACT_A' ? new Promise(resolve => { release = resolve }) : Promise.resolve('CONTRACT_TOKEN_B'))
  const older = t.widget.updatePatient(patient('CONTRACT_A'))
  await t.widget.updatePatient(patient('CONTRACT_B')); release('CONTRACT_TOKEN_A'); await older; await flush()
  assert.equal(t.calls.filter(call => call.url.endsWith('/launch-context')).length, 1)
  assert.equal(JSON.parse(t.calls[0].options.body).patient_id, 'CONTRACT_B')
  const frame = t.nodes.find(node => node.tag === 'iframe')
  t.nodes.find(node => node.tag === 'button').onclick()
  t.events.message({ origin: 'http://127.0.0.1:5175', source: frame.contentWindow, data: { type: 'CHEMO_READY' } })
  const last = t.messages.at(-1).message
  assert.equal(last.access_token, 'CONTRACT_TOKEN_B')
  assert.equal(last.context_id, 'CONTRACT_B')
  assert.equal(JSON.parse(t.calls[0].options.body).client_generation, 2)
  t.widget.destroy()
})
