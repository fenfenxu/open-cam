import "@testing-library/jest-dom/vitest";
import { createElement, type ReactNode } from "react";
import { beforeEach, vi } from "vitest";
import { nextNav } from "./next-nav";

vi.mock("next/navigation", async () => {
  const { nextNav: nav } = await import("./next-nav");
  return {
    usePathname: () => nav.pathname,
    useSearchParams: () => new URLSearchParams(nav.search),
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      prefetch: () => {},
    }),
    useParams: () => ({ slug: nav.pathname.split("/").filter(Boolean) }),
  };
});

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children?: ReactNode;
    className?: string;
  }) => createElement("a", { href, className }, children),
}));

beforeEach(() => {
  nextNav.pathname = "/";
  nextNav.search = "";
  nextNav.push.mockReset();
  nextNav.replace.mockReset();
});
