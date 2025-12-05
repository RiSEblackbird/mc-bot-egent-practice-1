// 日本語コメント：Bot 起動時の共通初期化をまとめたファクトリ
// 役割：設定読み込み・テレメトリ初期化・AgentBridge 生成を一括し、bot.ts からの依存注入を簡素化する。
import type { ConfigDependencies } from './config.js';
import { loadConfigValues, type ConfigLogger, type RuntimeConfigValues } from './configValues.js';
import {
  initializeTelemetry,
  type TelemetryContext,
} from './telemetryRuntime.js';
import { AgentBridge, type AgentBridgeDependencies, type AgentBridgeLogger } from './agentBridge.js';

export interface BootstrapOptions {
  env?: NodeJS.ProcessEnv;
  configLogger?: ConfigLogger;
  configDeps?: ConfigDependencies;
  agentBridgeLogger?: AgentBridgeLogger;
  agentBridgeSocketFactory?: AgentBridgeDependencies['createWebSocket'];
}

export interface BootstrapResult {
  configValues: RuntimeConfigValues;
  telemetry: TelemetryContext;
  agentBridge: AgentBridge;
}

/**
 * bot.ts からの初期化負担を減らし、外部依存を明確に注入するための共通ファクトリ。
 * 新規メンバーが依存の流れを追いやすいよう、設定→テレメトリ→AgentBridge の順で生成する。
 */
export function bootstrapRuntime(options: BootstrapOptions = {}): BootstrapResult {
  const configValues = loadConfigValues(options.env, options.configLogger, options.configDeps);
  const telemetry = initializeTelemetry(configValues.telemetry);

  const agentBridge = new AgentBridge(
    {
      url: configValues.agentBridge.url,
      connectTimeoutMs: configValues.agentBridge.connectTimeoutMs,
      sendTimeoutMs: configValues.agentBridge.sendTimeoutMs,
      healthcheckIntervalMs: configValues.agentBridge.healthcheckIntervalMs,
      reconnectDelayMs: configValues.agentBridge.reconnectDelayMs,
      maxRetries: configValues.agentBridge.maxRetries,
      batchFlushIntervalMs: configValues.agentBridge.batchFlushIntervalMs,
      batchMaxSize: configValues.agentBridge.batchMaxSize,
      queueMaxSize: configValues.agentBridge.queueMaxSize,
    },
    {
      tracer: telemetry.tracer,
      eventCounter: telemetry.agentBridgeEventCounter,
      logger: options.agentBridgeLogger,
      createWebSocket: options.agentBridgeSocketFactory,
    },
  );

  return { configValues, telemetry, agentBridge };
}
