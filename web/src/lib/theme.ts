/** 首屏主题必须与静态 HTML 一致。next-themes 服务端 theme 为 undefined，客户端第一帧会读 localStorage。 */

export function themeForSsr(
  theme: string | undefined,
  mounted: boolean,
  fallback = "system",
): string {
  if (!mounted) return fallback;
  return theme ?? fallback;
}
