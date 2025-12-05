// 日本語コメント：Bot 起動時に利用する設定値を1か所へ集約するヘルパー
// 役割：環境変数の読み出しとログ出力の副作用を局所化し、テスト容易性を高める
import { loadBotRuntimeConfig, type BotRuntimeConfig, type ConfigDependencies } from './config.js';

/**
 * 設定読み込み時に利用するロガーのインターフェース。
 * デフォルトでは console を利用するが、テストではモックを注入して副作用を抑止できる。
 */
export interface ConfigLogger {
  info(message: string): void;
  warn(message: string): void;
}

const defaultLogger: ConfigLogger = {
  info: (message: string) => console.log(message),
  warn: (message: string) => console.warn(message),
};

/**
 * Mineflayer ボットが参照する設定値をまとめた構造体。
 * runtime プロパティは環境変数からの生値を保持し、その他は利用頻度の高い派生値を格納する。
 */
export interface RuntimeConfigValues {
  runtime: BotRuntimeConfig;
  control: {
    mode: 'command' | 'vpt' | 'hybrid';
    vptCommandsEnabled: boolean;
    tickIntervalMs: number;
    maxSequenceLength: number;
  };
  minecraft: {
    host: string;
    port: number;
    version: string | undefined;
    reconnectDelayMs: number;
    username: string;
    authMode: 'offline' | 'microsoft';
  };
  websocket: {
    host: string;
    port: number;
  };
  agentBridge: BotRuntimeConfig['agentBridge'];
  moveGoalTolerance: BotRuntimeConfig['moveGoalTolerance'];
  skills: BotRuntimeConfig['skills'];
  telemetry: BotRuntimeConfig['telemetry'];
  perception: BotRuntimeConfig['perception'];
}

/**
 * 環境変数を読み込み、設定値と副作用的なログ出力をまとめて実行する。
 *
 * @param env 使用する環境変数。既定値は process.env。
 * @param logger ログ出力の委譲先。既定では console へ出力する。
 * @param deps Docker 判定などの依存。テストから差し替えて挙動を固定化できる。
 */
export function loadConfigValues(
  env: NodeJS.ProcessEnv = process.env,
  logger: ConfigLogger = defaultLogger,
  deps?: ConfigDependencies,
): RuntimeConfigValues {
  const { config, warnings } = loadBotRuntimeConfig(env, deps);

  for (const warning of warnings) {
    logger.warn(`[Config] ${warning}`);
  }

  const controlMode = config.control.mode;
  const controlValues = {
    mode: controlMode,
    vptCommandsEnabled: controlMode === 'vpt' || controlMode === 'hybrid',
    tickIntervalMs: config.control.vpt.tickIntervalMs,
    maxSequenceLength: config.control.vpt.maxSequenceLength,
  } as const;

  logger.info(
    `[Control] mode=${controlValues.mode} vptEnabled=${controlValues.vptCommandsEnabled} tick=${controlValues.tickIntervalMs}ms maxSeq=${controlValues.maxSequenceLength}`,
  );

  return {
    runtime: config,
    control: controlValues,
    minecraft: {
      host: config.minecraft.host,
      port: config.minecraft.port,
      version: config.minecraft.version,
      reconnectDelayMs: config.minecraft.reconnectDelayMs,
      username: config.minecraft.username,
      authMode: config.minecraft.authMode,
    },
    websocket: {
      host: config.websocket.host,
      port: config.websocket.port,
    },
    agentBridge: config.agentBridge,
    moveGoalTolerance: config.moveGoalTolerance,
    skills: config.skills,
    telemetry: config.telemetry,
    perception: config.perception,
  } satisfies RuntimeConfigValues;
}
