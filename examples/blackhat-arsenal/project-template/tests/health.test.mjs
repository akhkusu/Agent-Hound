import assert from 'node:assert/strict';
import test from 'node:test';

import { healthResponse } from '../src/api/health.mjs';

test('health response is stable', () => {
  assert.deepEqual(healthResponse(), {
    status: 'ok',
    service: 'acme-saas-demo',
  });
});
