/**
 * Mineflayer ボットが外部へ共有する各種スナップショットの型定義を集約するモジュール。
 *
 * ボット本体の処理ロジックから分離することで、依存方向を runtime 層にまとめつつ、
 * 新規アサインメンバーが型構造を素早く把握できるようにしている。
 */
import type { AgentRoleDescriptor } from './roles.js';

export type GatherStatusKind = 'position' | 'inventory' | 'general' | 'environment';

export interface PositionSnapshot {
  kind: 'position';
  position: { x: number; y: number; z: number };
  dimension: string;
  formatted: string;
}

export type NullableDurabilityValue = number | null;

export interface InventoryItemSnapshot {
  slot: number;
  name: string;
  displayName: string;
  count: number;
  enchantments: string[];
  maxDurability: NullableDurabilityValue;
  durabilityUsed: NullableDurabilityValue;
  durability: NullableDurabilityValue;
}

export interface InventorySnapshot {
  kind: 'inventory';
  occupiedSlots: number;
  totalSlots: number;
  items: InventoryItemSnapshot[];
  pickaxes: InventoryItemSnapshot[];
  formatted: string;
}

export interface DigPermissionSnapshot {
  allowed: boolean;
  gameMode: string;
  fallbackMovementInitialized: boolean;
  reason: string;
}

export interface GeneralStatusSnapshot {
  kind: 'general';
  health: number;
  maxHealth: number;
  food: number;
  saturation: number;
  oxygenLevel: number;
  digPermission: DigPermissionSnapshot;
  agentRole: AgentRoleDescriptor;
  formatted: string;
  perception?: PerceptionSnapshot | null;
}

export interface PositionReference {
  x: number;
  y: number;
  z: number;
  distance: number;
  bearing: string;
}

export interface NearbyEntitySummary {
  name: string;
  kind: string;
  distance: number;
  bearing: string;
  position: { x: number; y: number; z: number };
}

export interface HazardSummary {
  liquids: number;
  lava: number;
  magma: number;
  voids: number;
  warnings: string[];
  closestLiquid: PositionReference | null;
  closestVoid: PositionReference | null;
}

export interface LightingSummary {
  sky: number | null;
  block: number | null;
}

export interface WeatherSummary {
  isRaining: boolean;
  rainLevel: number;
  thunderLevel: number;
  label: string;
}

export interface PerceptionSnapshot {
  kind: 'perception';
  timestamp: number;
  position: { x: number; y: number; z: number; dimension: string };
  health?: number;
  food_level?: number;
  weather: WeatherSummary;
  time: {
    age: number;
    day: number;
    timeOfDay: number;
    isDay: boolean;
  };
  lighting: LightingSummary;
  hazards: HazardSummary;
  nearby_entities: {
    total: number;
    hostiles: number;
    players: number;
    details: NearbyEntitySummary[];
  };
  warnings: string[];
  summary?: string;
}

export interface EnvironmentSnapshot {
  kind: 'environment';
  perception: PerceptionSnapshot | null;
  role: AgentRoleDescriptor;
  eventQueueSize: number;
}

export type VptControlName =
  | 'forward'
  | 'back'
  | 'left'
  | 'right'
  | 'jump'
  | 'sprint'
  | 'sneak'
  | 'attack'
  | 'use';

export interface VptControlAction {
  kind: 'control';
  control: VptControlName;
  state: boolean;
  durationTicks: number;
}

export interface VptLookAction {
  kind: 'look';
  yaw: number;
  pitch: number;
  relative?: boolean;
  durationTicks?: number;
}

export interface VptWaitAction {
  kind: 'wait';
  durationTicks: number;
}

export type VptAction = VptControlAction | VptLookAction | VptWaitAction;

export interface VptNavigationHint {
  targetYawDegrees: number;
  horizontalDistance: number;
  verticalOffset: number;
}

export interface VptObservationHotbarSlot {
  slot: number;
  name: string;
  displayName: string;
  count: number;
}

export interface VptObservationSnapshot {
  position: { x: number; y: number; z: number };
  velocity: { x: number; y: number; z: number };
  orientation: { yawDegrees: number; pitchDegrees: number };
  status: { health: number; food: number; saturation: number };
  onGround: boolean;
  hotbar: VptObservationHotbarSlot[];
  heldItem: string | null;
  navigationHint: VptNavigationHint | null;
  timestamp: number;
  tickAge: number;
  dimension: string;
}

export interface RegisteredSkill {
  id: string;
  title: string;
  description: string;
  steps: string[];
  tags: string[];
  createdAt: number;
}

export interface FoodInfo {
  // minecraft-data 側の構造体では foodPoints / saturation 等が格納されている。
  // 本エージェントでは存在確認のみ行うため、詳細なフィールド定義は必須ではない。
  foodPoints?: number;
  saturation?: number;
}

export type FoodDictionary = Record<string, FoodInfo>;
