// shadcn 的类名合并入口：cn 同时负责条件拼接与 Tailwind 冲突消解，
// 官方已用零依赖的 cn 包取代 clsx + tailwind-merge（需要 Node >= 22）。
export { cn } from "cn";
