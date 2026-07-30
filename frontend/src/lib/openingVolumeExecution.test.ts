import assert from 'node:assert/strict'
import test from 'node:test'

import { participationRatio } from './openingVolumeExecution.ts'

test('opening-volume participation converts zero to unlimited and clamps percentages', () => {
  assert.equal(participationRatio('0'), 0)
  assert.equal(participationRatio('25'), 0.25)
  assert.equal(participationRatio('-5'), 0)
  assert.equal(participationRatio('150'), 1)
  assert.equal(participationRatio('not-a-number'), 0)
})
