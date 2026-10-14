/**
 * 公开资料路由的 Server 侧 layout —— 唯一目的是 `generateStaticParams`。
 *
 * 同 songs/[id]/lyrics：page.tsx 是客户端组件，用户 id 是运行时数据 → 空列表。
 * 静态导出要求动态段可枚举，缺此文件 `next build` 会失败（GAP-015 引入时漏掉）。
 */
export function generateStaticParams() {
  // 必须至少产出一个参数（Next 在 output:'export' 下拒绝空数组）。
  // 用一个**哨兵值**生成骨架页；真实 id/token 由客户端从 URL 读取，
  // 哨兵页本身只是静态占位（渲染后仍是「加载中 / 无效」态，不会误报成功）。
  return [{ id: "__export__" }];
}

export default function PublicProfileLayout({ children }: { children: React.ReactNode }) {
  return children;
}
