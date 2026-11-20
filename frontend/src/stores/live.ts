import { create } from "zustand";

type Connection = "connecting" | "open" | "closed";

interface LiveState {
  connection: Connection;
  /** 最近一次收到数据变化的时间。 */
  lastChange: Date | null;
  setConnection: (connection: Connection) => void;
  changed: () => void;
}

/** 实时更新连接的状态，侧栏据此提示页面是否自动刷新。 */
export const useLive = create<LiveState>()((set) => ({
  connection: "connecting",
  lastChange: null,
  setConnection: (connection) => set({ connection }),
  changed: () => set({ lastChange: new Date() }),
}));
