import assert from 'node:assert/strict'
import test from 'node:test'
import { matchField } from '../src/blueprint-fields.ts'
import type { FieldDefinition } from '../src/api.ts'

const field = (field_key: string, label: string) => ({ field_key, label } as FieldDefinition)
test('ANC shorthand binds its exact field without matching clinical instruction text', () => {
  const fields = [field('anc', '绝对中性粒细胞计数(ANC)')]
  assert.equal(matchField('ANC（×10⁹/L）：', fields)?.field_key, 'anc')
  assert.equal(matchField('ANC：小于0.5时需处理', fields), null)
})
test('total cycle guidance keeps an editable slot and unrelated instructions stay fixed', () => {
  const fields = [field('total_cycles', '总周期数'), field('height_cm', '身高(cm)')]
  assert.equal(matchField('总周期数：新辅助治疗4-6周期，术后继续辅助治疗', fields)?.field_key, 'total_cycles')
  assert.equal(matchField('身高（cm）：按原模板调整', fields), null)
  assert.equal(matchField('身高（cm）：____', fields)?.field_key, 'height_cm')
})
