// jsdom 26 的 @types 依赖较重；测试里只用最小 API 面（new JSDOM / window），
// 这里给出局部声明，避免为一条测试引入 @types/jsdom 全量依赖。
declare module "jsdom" {
  export class JSDOM {
    constructor(html?: string, options?: Record<string, unknown>);
    window: any;
  }
}
