/**
 * 分享路由的 Server 侧 layout —— 存在的唯一目的是提供 `generateStaticParams`。
 *
 * 为什么不能写在 page.tsx 里：page.tsx 是 `"use client"`（要用 useParams 读运行时
 * token），而 route segment config 只允许出现在 Server Component 中，写在客户端文件
 * 里会被当成不可静态解析的导出而报错。
 *
 * 为什么是空列表：分享 token 是运行时生成的 HMAC 签名，构建期无法预知。静态导出
 * （APK_BUILD=1 → output: 'export'）只产出「无 token」的骨架页，真实 token 由客户端
 * 从 URL 取并实时向后端校验。**缺了这个文件整个 `next build` 会失败** ——
 * #81 引入该路由时漏掉，导致 APK 静态导出静默挂掉（本次修复）。
 *
 * 对 Web 构建无影响：非 export 模式不做静态参数枚举。
 */
export function generateStaticParams() {
  // 必须至少产出一个参数（Next 在 output:'export' 下拒绝空数组）。
  // 用一个**哨兵值**生成骨架页；真实 id/token 由客户端从 URL 读取，
  // 哨兵页本身只是静态占位（渲染后仍是「加载中 / 无效」态，不会误报成功）。
  return [{ token: "__export__" }];
}

export default function ShareTokenLayout({ children }: { children: React.ReactNode }) {
  return children;
}
