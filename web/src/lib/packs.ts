export type PackCamera = {
  id: string;
  name: string;
};

export type PackRule = {
  name: string;
  camera?: string;
  type?: string;
};

export type SolutionPack = {
  id: string;
  name: string;
  origin: string;
  vertical: string;
  version: string;
  author?: string;
  description: string;
  cameras?: PackCamera[] | null;
  rules?: PackRule[];
};

export type PackApplyResult = {
  cameras?: { id: number; name?: string }[];
  rules?: { type: string; name?: string }[];
};

export function isLegacyPack(pack: SolutionPack): boolean {
  return pack.cameras == null;
}
