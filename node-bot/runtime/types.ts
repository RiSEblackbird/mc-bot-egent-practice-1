// 日本語コメント：WebSocket コマンドとエージェント間イベントの共通型定義
// 役割：コマンドルーティングやイベント配送を複数モジュールで共有するための一元型置き場。

export type CommandType =
  | 'chat'
  | 'moveTo'
  | 'equipItem'
  | 'gatherStatus'
  | 'gatherVptObservation'
  | 'mineOre'
  | 'setAgentRole'
  | 'registerSkill'
  | 'invokeSkill'
  | 'skillExplore'
  | 'playVptActions';

export interface CommandPayload {
  type: CommandType;
  args: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

export interface CommandResponse {
  ok: boolean;
  error?: string;
  data?: unknown;
}

export interface MultiAgentEventPayload {
  channel: 'multi-agent';
  event: 'roleUpdate' | 'position' | 'status' | 'perception';
  agentId: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

export interface AgentEventEnvelope {
  type: 'agentEvent';
  args: { event?: MultiAgentEventPayload; events?: MultiAgentEventPayload[] };
}

export type AgentBridgeSessionState = 'disconnected' | 'connecting' | 'connected';
