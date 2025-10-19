// 日本語コメント：Mineflayer 実行時の環境変数から一貫した設定オブジェクトを生成する
// 役割：bot.ts の肥大化を防ぎ、テストで個別条件を検証しやすくする
import minecraftData from 'minecraft-data';

import {
  AgentWebSocketResolution,
  HostResolutionResult,
  MoveGoalToleranceResolution,
  detectDockerRuntime,
  parseEnvInt,
  resolveAgentWebSocketEndpoint,
  resolveMinecraftHostValue,
  resolveMoveGoalTolerance,
} from './env.js';

/**
 * 依存関係を注入してテスト容易性を高めるためのインターフェース。
 * Docker 判定ロジックを差し替えることで、ユニットテストでも挙動を固定化できる。
 */
export interface ConfigDependencies {
  detectDockerRuntime(): boolean;
}

const defaultDependencies: ConfigDependencies = {
  detectDockerRuntime,
};

// Mineflayer と Paper サーバーの互換性を保つための既定バージョン。
const DEFAULT_MC_VERSION = '1.21.1';
const SUPPORTED_MINECRAFT_VERSIONS = new Set(
  minecraftData.versions.pc.map((version) => version.minecraftVersion),
);

/**
 * プロトコルバージョンの決定結果を表現する構造体。
 * Mineflayer へ渡すラベルと警告一覧をまとめることで、
 * 呼び出し元がログ出力やメトリクス送信を一元管理できるようにする。
 */
export interface MinecraftVersionResolution {
  version: string | undefined;
  warnings: string[];
}

/**
 * Mineflayer が接続時に利用するプロトコルバージョンを決定する。
 * サーバーとの不整合で PartialReadError が発生しないよう、minecraft-data が認識するラベルへ正規化する。
 */
export function resolveMinecraftVersionLabel(requestedVersionRaw: string | undefined): MinecraftVersionResolution {
  const warnings: string[] = [];
  const sanitized = (requestedVersionRaw ?? '').trim();

  if (sanitized.length === 0) {
    if (SUPPORTED_MINECRAFT_VERSIONS.has(DEFAULT_MC_VERSION)) {
      warnings.push(
        `環境変数 MC_VERSION が未設定のため、既定プロトコル ${DEFAULT_MC_VERSION} を利用します。`,
      );
      return { version: DEFAULT_MC_VERSION, warnings };
    }

    warnings.push(
      `環境変数 MC_VERSION が未設定ですが、既定プロトコル ${DEFAULT_MC_VERSION} が minecraft-data へ登録されていないため Mineflayer の自動判別に委ねます。`,
    );
    return { version: undefined, warnings };
  }

  if (SUPPORTED_MINECRAFT_VERSIONS.has(sanitized)) {
    return { version: sanitized, warnings };
  }

  if (SUPPORTED_MINECRAFT_VERSIONS.has(DEFAULT_MC_VERSION)) {
    warnings.push(
      `MC_VERSION='${sanitized}' は minecraft-data の対応一覧に存在しないため ${DEFAULT_MC_VERSION} へフォールバックします。`,
    );
    return { version: DEFAULT_MC_VERSION, warnings };
  }

  warnings.push(
    `MC_VERSION='${sanitized}' は minecraft-data の対応一覧に存在せず、既定プロトコル ${DEFAULT_MC_VERSION} も見つからないため Mineflayer の自動判別にフォールバックします。`,
  );
  return { version: undefined, warnings };
}

/**
 * Node.js ボットの起動に必要な設定を集約したデータ構造。
 * 単一のインターフェースにまとめることで、DI によるテストが容易になる。
 */
export interface BotRuntimeConfig {
  dockerDetected: boolean;
  minecraft: {
    host: string;
    port: number;
    version: string | undefined;
    reconnectDelayMs: number;
    username: string;
    authMode: 'offline' | 'microsoft';
    hostResolution: HostResolutionResult;
    versionResolution: MinecraftVersionResolution;
  };
  websocket: {
    host: string;
    port: number;
  };
  agentBridge: AgentWebSocketResolution;
  moveGoalTolerance: MoveGoalToleranceResolution;
}

export interface ConfigLoadResult {
  config: BotRuntimeConfig;
  warnings: string[];
}

/**
 * プロセス環境変数から Mineflayer 実行に必要な設定を構築する。
 *
 * @param env 読み込む環境変数集合。テスト時は疑似辞書を差し込んで検証できる。
 */
export function loadBotRuntimeConfig(
  env: NodeJS.ProcessEnv = process.env,
  deps: ConfigDependencies = defaultDependencies,
): ConfigLoadResult {
  const dockerDetected = deps.detectDockerRuntime();

  const versionResolution = resolveMinecraftVersionLabel(env.MC_VERSION);
  const hostResolution = resolveMinecraftHostValue(env.MC_HOST, dockerDetected);
  const moveGoalToleranceResolution = resolveMoveGoalTolerance(env.MOVE_GOAL_TOLERANCE);
  const agentResolution = resolveAgentWebSocketEndpoint(
    env.AGENT_WS_URL,
    env.AGENT_WS_HOST,
    env.AGENT_WS_PORT,
    dockerDetected,
  );

  const config: BotRuntimeConfig = {
    dockerDetected,
    minecraft: {
      host: hostResolution.host,
      port: parseEnvInt(env.MC_PORT, 25565),
      version: versionResolution.version,
      reconnectDelayMs: parseEnvInt(env.MC_RECONNECT_DELAY_MS, 5000),
      username: env.BOT_USERNAME ?? 'HelperBot',
      authMode: (env.AUTH_MODE ?? 'offline') as 'offline' | 'microsoft',
      hostResolution,
      versionResolution,
    },
    websocket: {
      host: env.WS_HOST?.trim() && env.WS_HOST.trim().length > 0 ? env.WS_HOST.trim() : '0.0.0.0',
      port: parseEnvInt(env.WS_PORT, 8765),
    },
    agentBridge: agentResolution,
    moveGoalTolerance: moveGoalToleranceResolution,
  };

  const warnings: string[] = [
    ...versionResolution.warnings,
    ...agentResolution.warnings,
    ...moveGoalToleranceResolution.warnings,
  ];

  if (hostResolution.usedDockerFallback && hostResolution.originalValue.length > 0) {
    warnings.push(
      'MC_HOST points to localhost inside Docker. Falling back to host.docker.internal so the Paper server is reachable.',
    );
  }

  if (config.websocket.host === '0.0.0.0' && env.WS_HOST && env.WS_HOST.trim() === '0.0.0.0') {
    warnings.push('WS_HOST=0.0.0.0 は受信専用アドレスです。Python 側から接続する際は AGENT_WS_HOST を利用してください。');
  }

  return { config, warnings };
}
