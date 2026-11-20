import { useSearchParams } from "react-router";

/** 筛选和分页写进地址栏，刷新和分享链接时保持不变；改筛选条件时回到第一页。 */
export function useFilters<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(
    keys.map((key) => [key, params.get(key) ?? undefined]),
  ) as Record<K, string | undefined>;
  const page = Number(params.get("page") ?? 1);
  const set = (changes: Partial<Record<K | "page", string | number | undefined>>) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (!("page" in changes)) next.delete("page");
        for (const [key, value] of Object.entries(changes)) {
          if (value === undefined || value === "") next.delete(key);
          else next.set(key, String(value));
        }
        return next;
      },
      { replace: true },
    );
  return { values, page, set };
}
