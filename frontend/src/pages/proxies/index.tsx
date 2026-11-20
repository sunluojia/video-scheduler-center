import type { RowSelectionState } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/page";
import { ProxyAlerts } from "@/components/alerts";
import { Button } from "@/components/ui/button";
import { UseBoard } from "./board";
import { ImportDialog, ProxyDialog } from "./dialogs";
import { Pool } from "./pool";
import { FolderTree } from "./tree";

/** 代理页：上面是各用途怎么走、线路好不好；下面左边文件夹、右边代理池。 */
export function ProxiesPage() {
  const [selection, setSelection] = useState<RowSelectionState>({});
  return (
    <>
      <PageHeader
        eyebrow="Proxies"
        title="代理"
        meta="通用代理池：给代理打上用途，爬取程序做对应任务前向调度中心申请一条线路，用完归还并报告结果。同一个代理可以用于多个用途，好坏按用途分开记。"
        actions={
          <>
            <ImportDialog />
            <ProxyDialog
              trigger={
                <Button>
                  <Plus /> 添加代理
                </Button>
              }
            />
          </>
        }
      />
      <ProxyAlerts />
      <UseBoard />
      <div className="grid items-start gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <FolderTree onMoved={() => setSelection({})} />
        <Pool selection={selection} onSelectionChange={setSelection} />
      </div>
    </>
  );
}
