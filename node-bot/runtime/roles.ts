/**
 * エージェントの役割定義と簡易ステート管理に関する補助モジュール。
 *
 * LangGraph からの役割切替要求を Node 側で安全に扱うため、
 * 役割のカタログと解決処理を一元化しておく。
 */

export type AgentRoleId = 'generalist' | 'defender' | 'supplier' | 'scout';

export interface AgentRoleDescriptor {
  /** 内部識別子。LangGraph からの要求とイベント連携で利用する。 */
  id: AgentRoleId;
  /** プレイヤーや開発者がログから読みやすい表示名。 */
  label: string;
  /** 役割が主に担当する業務カテゴリの概要。 */
  responsibilities: string[];
}

export interface AgentRoleState {
  /** 現在アクティブな役割の定義。 */
  activeRole: AgentRoleDescriptor;
  /** 最後に役割が更新されたイベント ID。 */
  lastEventId: string;
  /** ミリ秒精度での最終更新時刻。 */
  lastUpdatedAt: number;
  /** 直近に共有メモリへ送信した座標スナップショット。 */
  lastBroadcastPosition?: { x: number; y: number; z: number };
}

const ROLE_CATALOG: Record<AgentRoleId, AgentRoleDescriptor> = {
  generalist: {
    id: 'generalist',
    label: '汎用サポーター',
    responsibilities: ['状況適応', '検出タスク', '軽作業'],
  },
  defender: {
    id: 'defender',
    label: '防衛支援',
    responsibilities: ['敵対 Mob の警戒', '護衛移動', '退避誘導'],
  },
  supplier: {
    id: 'supplier',
    label: '補給調整',
    responsibilities: ['資材回収', '補給合流', '補充配布'],
  },
  scout: {
    id: 'scout',
    label: '先行偵察',
    responsibilities: ['地形確認', '危険検知', 'ルート探索'],
  },
};

/**
 * 未知の役割 ID が渡された際は、汎用ロールへ安全にフォールバックする。
 */
export function resolveAgentRole(rawId: string | undefined): AgentRoleDescriptor {
  const normalized = (rawId ?? '').trim().toLowerCase();
  if (normalized && normalized in ROLE_CATALOG) {
    return ROLE_CATALOG[normalized as AgentRoleId];
  }
  return ROLE_CATALOG.generalist;
}

export function createInitialAgentRoleState(): AgentRoleState {
  const activeRole = ROLE_CATALOG.generalist;
  return {
    activeRole,
    lastEventId: 'initial',
    lastUpdatedAt: Date.now(),
  };
}

export function getRoleCatalog(): AgentRoleDescriptor[] {
  return Object.values(ROLE_CATALOG);
}
