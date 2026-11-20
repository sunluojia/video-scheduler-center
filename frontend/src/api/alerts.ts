import { $api } from "./client";

/** 需要人处理的代理问题。设备上下线不发通知，所以定时刷新。 */
export function useProxyAlerts() {
  return $api.useQuery("get", "/api/proxy-alerts", {}, { refetchInterval: 30_000 });
}

/** 各设备的身份池和需要人处理的问题，随爬取程序每 15 秒的心跳更新。 */
export function useIdentities() {
  return $api.useQuery("get", "/api/identities", {}, { refetchInterval: 15_000 });
}
