/** Three-way rebase. A concurrent edit of the same value needs an explicit choice. */
export interface EditCollision { key: string; before: string; local: string; latest: string }
export function rebaseValues(base: Record<string, string>, local: Record<string, string>, latest: Record<string, string>, choices: Record<string, 'local' | 'latest'> = {}) {
  const values = { ...latest }
  const collisions: EditCollision[] = []
  for (const key of Object.keys(local)) {
    const before = base[key] ?? '', next = local[key] ?? '', remote = latest[key] ?? ''
    if (next === before) continue
    if (remote !== before && remote !== next) {
      collisions.push({ key, before, local: next, latest: remote })
      if (!choices[key] || choices[key] === 'latest') continue
    }
    values[key] = next
  }
  return { values, collisions, unresolved: collisions.filter(item => !choices[item.key]) }
}
