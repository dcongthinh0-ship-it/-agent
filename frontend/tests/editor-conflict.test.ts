import test from 'node:test'
import assert from 'node:assert/strict'
import { rebaseValues } from '../src/editor-conflict.ts'

test('separate doctor edits are retained without overwriting remote values', () => {
  const result = rebaseValues({ cycle: '1', note: '' }, { cycle: '2', note: '' }, { cycle: '1', note: '另一位医生已核对' })
  assert.deepEqual(result.values, { cycle: '2', note: '另一位医生已核对' })
  assert.equal(result.unresolved.length, 0)
})
test('same field conflict requires a deliberate choice and preserves all three values', () => {
  const base = { dose: '100' }, local = { dose: '80' }, latest = { dose: '90' }
  const result = rebaseValues(base, local, latest)
  assert.equal(result.unresolved.length, 1)
  assert.deepEqual(result.collisions[0], { key: 'dose', before: '100', local: '80', latest: '90' })
  assert.equal(rebaseValues(base, local, latest, { dose: 'local' }).values.dose, '80')
  assert.equal(rebaseValues(base, local, latest, { dose: 'latest' }).values.dose, '90')
})
test('same result from both doctors needs no conflict and blank is an intentional edit', () => {
  const result = rebaseValues({ dose: '100', note: '原说明' }, { dose: '80', note: '' }, { dose: '80', note: '原说明' })
  assert.equal(result.unresolved.length, 0)
  assert.deepEqual(result.values, { dose: '80', note: '' })
})
