import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = fileURLToPath(new URL('../../src/', import.meta.url))

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(path) ? [path] : []
  })
}

test('pages and features use domain APIs and contain no simulated delivery or patient presets', () => {
  for (const directory of ['pages', 'features']) {
    for (const path of sourceFiles(join(sourceRoot, directory))) {
      const source = readFileSync(path, 'utf8')
      assert.doesNotMatch(source, /\/api\/v1\//, path)
      assert.doesNotMatch(source, /DemoDeliveryPanel|\/demo\/|fillExample|demoMode/, path)
    }
  }
})
