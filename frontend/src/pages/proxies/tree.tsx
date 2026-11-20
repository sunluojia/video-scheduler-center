import {
  ChevronRight,
  Folder as FolderIcon,
  FolderPlus,
  Inbox,
  Layers,
  Ellipsis,
} from "lucide-react";
import { type DragEvent, type FormEvent, type ReactNode, useState } from "react";
import { $api } from "@/api/client";
import { Confirm } from "@/components/confirm";
import { Panel } from "@/components/page";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useFilters } from "@/lib/params";
import { useDone } from "@/lib/refresh";
import { cn } from "@/lib/utils";
import { depths, type Folder, PROXY_DRAG, subtree, UNFILED, useFolders } from "./data";

type Zone = "before" | "inside" | "after";
type Naming = { folder?: Folder; parent: string | null };

const FOLDER_DRAG = "application/x-proxy-folder";

/**
 * 左侧文件夹树：点选筛代理；代理拖到文件夹上换文件夹，
 * 文件夹拖到另一个文件夹中间放进去、拖到上下边缘调整先后（优先使用按这个顺序）。
 */
export function FolderTree({ onMoved }: { onMoved: () => void }) {
  const { values, set } = useFilters(["folder"] as const);
  const query = useFolders();
  const folders = query.data?.folders ?? [];
  const [closed, setClosed] = useState(new Set<string>());
  const [dragged, setDragged] = useState<string | null>(null);
  const [target, setTarget] = useState<{ id: string; zone: Zone } | null>(null);
  const [naming, setNaming] = useState<Naming>();
  const [removing, setRemoving] = useState<Folder>();
  const done = useDone();
  const moveFolder = $api.useMutation("post", "/api/proxy-folders/{folder_id}/move", {
    onSuccess: () => done("已移动文件夹"),
  });
  const moveProxies = $api.useMutation("post", "/api/proxies/move");
  const remove = $api.useMutation("delete", "/api/proxy-folders/{folder_id}", {
    onSuccess: () => done("已删除文件夹，里面的内容移到了上一级"),
  });

  const depth = depths(folders);
  const total = (id: string) =>
    folders
      .filter((item) => subtree(folders, id).has(item.id))
      .reduce((sum, item) => sum + item.count, 0);
  const all = folders.reduce((sum, item) => sum + item.count, query.data?.unfiled ?? 0);
  const hidden = new Set<string>();
  for (const folder of folders) {
    if (folder.parent_id && (hidden.has(folder.parent_id) || closed.has(folder.parent_id))) {
      hidden.add(folder.id);
    }
  }
  const toggle = (id: string) =>
    setClosed((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  /** 能不能放到这里：代理只能放进文件夹或“未分组”；文件夹不能放进自己下面。 */
  const accepts = (event: DragEvent, id: string, proxies: boolean, folder: boolean) => {
    const types = event.dataTransfer.types;
    if (types.includes(PROXY_DRAG)) return proxies;
    return folder && types.includes(FOLDER_DRAG) && !(dragged && subtree(folders, dragged).has(id));
  };
  const over = (event: DragEvent<HTMLElement>, id: string, zones: boolean) => {
    event.preventDefault();
    const { top, height } = event.currentTarget.getBoundingClientRect();
    const y = (event.clientY - top) / height;
    const zone =
      !zones || event.dataTransfer.types.includes(PROXY_DRAG)
        ? "inside"
        : y < 0.3
          ? "before"
          : y > 0.7
            ? "after"
            : "inside";
    setTarget({ id, zone });
  };
  const drop = (event: DragEvent, folder: Folder | null) => {
    event.preventDefault();
    const zone = target?.zone ?? "inside";
    setTarget(null);
    const ids = event.dataTransfer.getData(PROXY_DRAG);
    if (ids) {
      return moveProxies.mutate(
        { body: { ids: JSON.parse(ids), folder_id: folder?.id ?? null } },
        {
          onSuccess: (result) => {
            done(`已把 ${result.count} 个代理移到${folder ? `“${folder.name}”` : "未分组"}`);
            onMoved();
          },
        },
      );
    }
    if (!dragged) return;
    const parent = zone === "inside" ? (folder?.id ?? null) : folder!.parent_id;
    const siblings = folders.filter((item) => item.parent_id === parent && item.id !== dragged);
    const position =
      zone === "inside" ? siblings.length : siblings.indexOf(folder!) + (zone === "after" ? 1 : 0);
    moveFolder.mutate({
      params: { path: { folder_id: dragged } },
      body: { parent_id: parent, position },
    });
  };

  return (
    <Panel className="rise self-start p-2">
      <div className="flex items-center justify-between px-2 pt-1 pb-2">
        <h3 className="text-sm font-semibold">文件夹</h3>
        <Button size="xs" variant="ghost" onClick={() => setNaming({ parent: null })}>
          <FolderPlus /> 新建
        </Button>
      </div>
      <div role="tree" aria-label="代理文件夹" className="space-y-px">
        <Row
          icon={<Layers />}
          label="全部代理"
          count={all}
          active={!values.folder}
          onSelect={() => set({ folder: undefined })}
          drop={target?.id === "all" ? "inside" : undefined}
          onDragOver={(event) => accepts(event, "all", false, true) && over(event, "all", false)}
          onDragLeave={() => setTarget(null)}
          onDrop={(event) => drop(event, null)}
        />
        {folders
          .filter((folder) => !hidden.has(folder.id))
          .map((folder) => {
            const parent = folders.some((item) => item.parent_id === folder.id);
            return (
              <Row
                key={folder.id}
                icon={<FolderIcon />}
                label={folder.name}
                count={total(folder.id)}
                depth={(depth.get(folder.id) ?? 0) + 1}
                active={values.folder === folder.id}
                open={parent ? !closed.has(folder.id) : undefined}
                onToggle={() => toggle(folder.id)}
                onSelect={() => set({ folder: folder.id })}
                drop={target?.id === folder.id ? target.zone : undefined}
                draggable
                dragging={dragged === folder.id}
                onDragStart={(event) => {
                  event.dataTransfer.setData(FOLDER_DRAG, folder.id);
                  event.dataTransfer.effectAllowed = "move";
                  setDragged(folder.id);
                }}
                onDragEnd={() => {
                  setDragged(null);
                  setTarget(null);
                }}
                onDragOver={(event) =>
                  accepts(event, folder.id, true, true) && over(event, folder.id, true)
                }
                onDragLeave={() => setTarget(null)}
                onDrop={(event) => drop(event, folder)}
                menu={
                  <FolderMenu
                    name={folder.name}
                    onAdd={() => setNaming({ parent: folder.id })}
                    onRename={() => setNaming({ folder, parent: folder.parent_id })}
                    onRemove={() => setRemoving(folder)}
                  />
                }
              />
            );
          })}
        <Row
          icon={<Inbox />}
          label="未分组"
          count={query.data?.unfiled ?? 0}
          active={values.folder === UNFILED}
          onSelect={() => set({ folder: UNFILED })}
          drop={target?.id === UNFILED ? "inside" : undefined}
          onDragOver={(event) =>
            accepts(event, UNFILED, true, false) && over(event, UNFILED, false)
          }
          onDragLeave={() => setTarget(null)}
          onDrop={(event) => drop(event, null)}
        />
      </div>
      <p className="text-muted-foreground px-2 pt-3 pb-1 text-xs leading-relaxed">
        勾选代理后拖动左侧把手可整批换文件夹；文件夹拖到另一个文件夹上放进去，拖到上下边缘调整先后。
      </p>
      {naming && <NameDialog naming={naming} onClose={() => setNaming(undefined)} />}
      <Confirm
        open={Boolean(removing)}
        onOpenChange={(open) => !open && setRemoving(undefined)}
        title="删除文件夹"
        description={`只删除“${removing?.name}”这个文件夹：里面的代理和子文件夹移到上一级，用它做范围的设置改为上一级。`}
        action="删除"
        onConfirm={() => {
          remove.mutate({ params: { path: { folder_id: removing!.id } } });
          if (values.folder === removing!.id) set({ folder: undefined });
        }}
      />
    </Panel>
  );
}

function Row({
  icon,
  label,
  count,
  depth = 0,
  active,
  open,
  onToggle,
  onSelect,
  drop,
  dragging,
  menu,
  ...drag
}: {
  icon: ReactNode;
  label: string;
  count: number;
  depth?: number;
  active: boolean;
  /** 有子文件夹时是否展开；没有子文件夹时不传。 */
  open?: boolean;
  onToggle?: () => void;
  onSelect: () => void;
  drop?: Zone;
  dragging?: boolean;
  menu?: ReactNode;
  draggable?: boolean;
  onDragStart?: (event: DragEvent) => void;
  onDragEnd?: () => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent) => void;
}) {
  return (
    <div
      role="treeitem"
      aria-selected={active}
      aria-expanded={open}
      {...drag}
      onClick={onSelect}
      style={{ paddingLeft: `${depth * 0.9 + 0.25}rem` }}
      className={cn(
        "group relative flex h-8 cursor-pointer items-center gap-1.5 rounded-md pr-1 text-sm select-none",
        active
          ? "bg-signal/15 text-foreground font-medium"
          : "hover:bg-muted text-muted-foreground",
        drop === "inside" && "ring-signal bg-signal/10 ring-2 ring-inset",
        dragging && "opacity-40",
        drop === "before" &&
          "before:bg-signal before:absolute before:inset-x-1 before:top-0 before:h-0.5",
        drop === "after" &&
          "after:bg-signal after:absolute after:inset-x-1 after:bottom-0 after:h-0.5",
      )}
    >
      <button
        type="button"
        tabIndex={-1}
        aria-label={open ? "收起" : "展开"}
        disabled={open === undefined}
        onClick={(event) => {
          event.stopPropagation();
          onToggle?.();
        }}
        className="grid size-4 shrink-0 place-items-center disabled:invisible"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
      </button>
      <span className="[&_svg]:size-4 [&_svg]:shrink-0">{icon}</span>
      <span className="text-foreground min-w-0 flex-1 truncate">{label}</span>
      <span
        className={cn(
          "text-muted-foreground font-mono text-xs tabular-nums",
          menu && "group-hover:hidden group-has-[[data-state=open]]:hidden",
        )}
      >
        {count}
      </span>
      {menu}
    </div>
  );
}

function FolderMenu({
  name,
  onAdd,
  onRename,
  onRemove,
}: {
  name: string;
  onAdd: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`${name} 的操作`}
          onClick={(event) => event.stopPropagation()}
          className="hidden group-hover:inline-flex data-[state=open]:inline-flex"
        >
          <Ellipsis />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" onClick={(event) => event.stopPropagation()}>
        <DropdownMenuItem onSelect={onAdd}>新建子文件夹</DropdownMenuItem>
        <DropdownMenuItem onSelect={onRename}>重命名</DropdownMenuItem>
        <DropdownMenuItem variant="destructive" onSelect={onRemove}>
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 新建或重命名文件夹；同一层里不能重名。 */
function NameDialog({ naming, onClose }: { naming: Naming; onClose: () => void }) {
  const done = useDone();
  const folders = useFolders().data?.folders ?? [];
  const parent = folders.find((folder) => folder.id === naming.parent)?.name;
  const onSuccess = () => {
    done(naming.folder ? "已重命名" : "已新建文件夹");
    onClose();
  };
  const add = $api.useMutation("post", "/api/proxy-folders", { onSuccess });
  const rename = $api.useMutation("patch", "/api/proxy-folders/{folder_id}", { onSuccess });
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name")).trim();
    if (naming.folder) {
      rename.mutate({ params: { path: { folder_id: naming.folder.id } }, body: { name } });
    } else {
      add.mutate({ body: { name, parent_id: naming.parent } });
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{naming.folder ? "重命名文件夹" : "新建文件夹"}</DialogTitle>
          <DialogDescription>
            {naming.folder
              ? "同一层里不能重名。"
              : parent
                ? `放在“${parent}”下面。`
                : "放在最外层。"}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Input
            name="name"
            aria-label="文件夹名"
            placeholder="比如 美国住宅、日本机房"
            defaultValue={naming.folder?.name}
            maxLength={64}
            required
            autoFocus
          />
          <DialogFooter>
            <Button type="submit" disabled={add.isPending || rename.isPending}>
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
