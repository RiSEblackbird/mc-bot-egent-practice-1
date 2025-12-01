// 日本語コメント：環境変数の安全なパースに関する共通ユーティリティ
// 役割：NaN に起因するバグを防ぐため、フォールバックを伴うシンプルな数値パーサーを提供する

/**
 * 数値型環境変数を安全に読み込むユーティリティ。
 * 数値化に失敗した場合はフォールバック値を返し、NaN による予期しない動作を防ぐ。
 */
export function parseEnvInt(rawValue: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(rawValue ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}
