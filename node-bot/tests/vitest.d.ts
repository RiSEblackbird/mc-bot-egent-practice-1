/**
 * Vitest の型定義を簡易的に補完する宣言ファイル。
 * Node.js 22 未満の環境では devDependencies のインストールが制限されるため、
 * テストコードの import 文が TypeScript コンパイルエラーにならないよう any 型で定義する。
 */
declare module 'vitest' {
  export const describe: (...args: unknown[]) => any;
  export const it: (...args: unknown[]) => any;
  interface ExpectStatic {
    (...args: unknown[]): any;
    arrayContaining: (...args: unknown[]) => any;
  }
  export const expect: ExpectStatic;
}
