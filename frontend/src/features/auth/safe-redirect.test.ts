import { describe, expect, it } from 'vitest'
import { internalPathFrom } from './safe-redirect'

describe('internal authentication redirects', () => {
  it('preserves an internal path with search and hash', () => {
    expect(internalPathFrom({ from: '/documents?sort=name#recent' })).toBe('/documents?sort=name#recent')
  })

  it.each(['https://attacker.example/path', '//attacker.example/path', '/\\attacker.example', 'not-a-path'])(
    'rejects an unsafe return destination: %s',
    (from) => expect(internalPathFrom({ from })).toBeNull(),
  )
})
