import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useLive } from "@/stores/live";

const THROTTLE_MS = 1000;
/** 不跟着业务数据刷新的接口：AI 配置不随业务变化；数据看板统计量大，按自己的间隔刷新。 */
const SELF_PACED = /^\/api\/(ai\/|stats)/;

/**
 * 订阅后端的数据变化：收到通知后让页面上的查询重新获取。
 * 断线由浏览器自动重连，重连后整体刷新一次。
 */
export function useLiveUpdates() {
  const queryClient = useQueryClient();
  const { setConnection, changed } = useLive();

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      changed();
      timer ??= setTimeout(() => {
        timer = undefined;
        void queryClient.invalidateQueries({
          predicate: (query) => !SELF_PACED.test(String(query.queryKey[1])),
        });
      }, THROTTLE_MS);
    };
    const source = new EventSource("/api/live");
    let opened = false;
    source.onopen = () => {
      setConnection("open");
      if (opened) refresh(); // 断线期间的变化收不到通知
      opened = true;
    };
    source.onerror = () =>
      setConnection(source.readyState === EventSource.CLOSED ? "closed" : "connecting");
    source.addEventListener("changed", refresh);
    return () => {
      clearTimeout(timer);
      source.close();
    };
  }, [queryClient, setConnection, changed]);
}
