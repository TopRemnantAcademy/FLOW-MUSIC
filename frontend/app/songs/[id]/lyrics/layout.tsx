/**
 * 歌词编辑路由的 Server 侧 layout —— 唯一目的是 `generateStaticParams`。
 *（`id` 段在 Next 的静态导出里必须能枚举，故给一个哨兵值。）
 *
 * page.tsx 是 `"use client"`（需 useParams 读运行时 id），而 route segment config
 * 只能出现在 Server Component 里。静态导出（APK_BUILD=1）要求动态段可枚举；
 * 曲目 id 是运行时数据，故返回空列表：导出只产出骨架页，真实 id 由客户端从 URL 取。
 * 缺了这个文件整次 `next build` 会失败（#80 引入该路由时漏掉）。
 */
export function generateStaticParams() {
  // 必须至少产出一个参数（Next 在 output:'export' 下拒绝空数组）。
  // 用一个**哨兵值**生成骨架页；真实 id/token 由客户端从 URL 读取，
  // 哨兵页本身只是静态占位（渲染后仍是「加载中 / 无效」态，不会误报成功）。
  return [{ id: "__export__" }];
}

export default function LyricsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
