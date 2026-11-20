import { AlertTriangle } from "lucide-react";
import { useIdentities, useProxyAlerts } from "@/api/alerts";

/** 需要人处理的问题，一条一行。 */
function Alerts({ items }: { items: string[] | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="rise border-status-attention/50 bg-status-attention/10 space-y-1.5 rounded-lg border px-4 py-3 text-sm">
      {items.map((text) => (
        <p key={text} className="flex items-center gap-2">
          <AlertTriangle className="text-status-attention size-4 shrink-0" />
          {text}
        </p>
      ))}
    </div>
  );
}

export function ProxyAlerts() {
  return <Alerts items={useProxyAlerts().data} />;
}

export function IdentityAlerts() {
  return <Alerts items={useIdentities().data?.alerts} />;
}
