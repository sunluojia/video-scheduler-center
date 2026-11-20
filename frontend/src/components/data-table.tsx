import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  type OnChangeFn,
  type RowSelectionState,
  useReactTable,
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/**
 * 表格：分页和筛选在服务端完成，这里只负责展示、点击行和勾选行。
 * 列定义要放在组件外面保持不变，否则每次渲染单元格都会重新挂载，里面打开的对话框会被关掉。
 */
export function DataTable<T>({
  columns,
  data,
  onRowClick,
  rowClassName,
  getRowId,
  selection,
  onSelectionChange,
}: {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  getRowId?: (row: T) => string;
  /** 勾选的行，按行 ID 记录，翻页后仍保留。 */
  selection?: RowSelectionState;
  onSelectionChange?: OnChangeFn<RowSelectionState>;
}) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    state: { rowSelection: selection ?? {} },
    onRowSelectionChange: onSelectionChange,
    enableRowSelection: Boolean(onSelectionChange),
  });
  return (
    <Table>
      <TableHeader>
        {table.getHeaderGroups().map((group) => (
          <TableRow key={group.id} className="hover:bg-transparent">
            {group.headers.map((header) => (
              <TableHead
                key={header.id}
                className="text-muted-foreground h-9 text-xs font-medium"
                style={{ width: header.column.columnDef.size }}
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
              </TableHead>
            ))}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow
            key={row.id}
            onClick={onRowClick && (() => onRowClick(row.original))}
            className={cn(onRowClick && "cursor-pointer", rowClassName?.(row.original))}
          >
            {row.getVisibleCells().map((cell) => (
              <TableCell key={cell.id} className="py-2.5 align-top">
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** 分页：页码从 1 开始。 */
export function Pager({
  page,
  size,
  total,
  onPage,
}: {
  page: number;
  size: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="text-muted-foreground flex items-center justify-between border-t px-4 py-2.5 text-xs">
      <span>
        共 <span className="text-foreground font-mono">{total}</span> 条
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="上一页"
        >
          <ChevronLeft />
        </Button>
        <span className="font-mono">
          {page} / {pages}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label="下一页"
        >
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
