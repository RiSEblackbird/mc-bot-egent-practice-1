// 日本語コメント：従来互換のための薄いエイリアスモジュール
// 役割：新設した telemetryRuntime から初期化関数を再エクスポートし、
//       既存 import パスを利用するテストやスクリプトを壊さないようにする。
export * from './telemetryRuntime.js';
