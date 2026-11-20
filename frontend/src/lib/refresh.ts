import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

/** 操作成功后提示并刷新页面数据；执行程序没收到通知时一并提示。 */
export function useDone() {
  const queryClient = useQueryClient();
  return (message: string, warnings: string[] = []) => {
    toast.success(message);
    for (const warning of warnings) toast.warning(warning);
    void queryClient.invalidateQueries();
  };
}
