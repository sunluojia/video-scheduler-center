import { $api, type Schemas } from "@/api/client";

export type Folder = Schemas["FolderView"];
export type UseCard = Schemas["UseView"];
export type Tag = Schemas["Service"] | Schemas["Use"];

/** 地址栏里“未分组”的写法；文件夹参数为空表示全部代理。 */
export const UNFILED = "none";

/** 从代理表拖出的代理 ID（JSON 数组）。 */
export const PROXY_DRAG = "application/x-proxy-ids";

/** 主机加端口；IPv6 主机加方括号。 */
export const address = (host: string, port: number) =>
  host.includes(":") ? `[${host}]:${port}` : `${host}:${port}`;

/** 代理的显示名：有备注用备注，否则用地址。 */
export const proxyName = (proxy: { name: string; host: string; port: number }) =>
  proxy.name || address(proxy.host, proxy.port);

/** 各用途的卡片：规则、线路和设备。线路的冷却时间一直在变，定时刷新。 */
export function useUses() {
  return $api.useQuery("get", "/api/proxy-uses", {}, { refetchInterval: 5_000 });
}

export function useFolders() {
  return $api.useQuery("get", "/api/proxy-folders");
}

/** 用途标签的名称：整个服务写服务名，具体任务写“服务 · 任务”。 */
export function useTagName() {
  const uses = useUses().data ?? [];
  return (tag: string) => {
    const use = uses.find((item) => item.key === tag);
    if (use) return use.name;
    return uses.find((item) => item.service === tag)?.service_name ?? tag;
  };
}

/** 文件夹的层级：接口按树的先后顺序返回，父文件夹总在子文件夹前面。 */
export function depths(folders: Folder[]): Map<string, number> {
  const depth = new Map<string, number>();
  for (const folder of folders) {
    depth.set(folder.id, folder.parent_id ? (depth.get(folder.parent_id) ?? 0) + 1 : 0);
  }
  return depth;
}

/** 这个文件夹和它下面的所有文件夹。 */
export function subtree(folders: Folder[], root: string): Set<string> {
  const found = new Set([root]);
  for (const folder of folders) {
    if (folder.parent_id && found.has(folder.parent_id)) found.add(folder.id);
  }
  return found;
}
