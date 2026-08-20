import CatchAllPage from "./client";

export function generateStaticParams() {
  return [
    { slug: [] },
    { slug: ["cameras"] },
    { slug: ["rules"] },
    { slug: ["events"] },
    { slug: ["training"] },
    { slug: ["marketplace"] },
    { slug: ["settings"] },
  ];
}

export default function Page() {
  return <CatchAllPage />;
}
