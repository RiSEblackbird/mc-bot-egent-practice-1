import { describe, expect, it } from 'vitest';

import { isLiquidConflict, type BridgeStopPayload } from '../runtime/bridgeErrors';

describe('isLiquidConflict', () => {
  it('returns true for liquid 409 payloads', () => {
    const payload: BridgeStopPayload = { error: 'liquid_detected', stop: true };
    expect(isLiquidConflict(409, payload)).toBe(true);
  });

  it('returns false for non-liquid 409 payloads', () => {
    const payload: BridgeStopPayload = { error: 'other_error', stop: false };
    expect(isLiquidConflict(409, payload)).toBe(false);
  });

  it('returns false for other status codes', () => {
    const payload: BridgeStopPayload = { error: 'liquid_detected', stop: true };
    expect(isLiquidConflict(200, payload)).toBe(false);
  });
});
