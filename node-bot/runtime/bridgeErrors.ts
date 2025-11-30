/**
 * AgentBridge HTTP のエラー応答から安全停止が必要かどうかを判定するヘルパー。
 * Mineflayer が液体に遭遇した際の 409 応答を早期に検知し、
 * 追加の採掘コマンドを送らないよう呼び出し元で分岐させることを想定している。
 */
export type BridgeStopPayload = {
  error?: string;
  stop?: boolean;
  stop_pos?: { x: number; y: number; z: number };
  job_id?: string;
};

export function isLiquidConflict(status: number, payload?: BridgeStopPayload | null): boolean {
  if (status !== 409 || !payload) {
    return false;
  }
  return payload.stop === true || payload.error === 'liquid_detected';
}
