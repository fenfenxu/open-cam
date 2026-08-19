import { useMemo } from 'react'
import { FlexRender } from '@tanstack/react-table'
import {
  getCoreRowModel,
  useLegacyTable,
  type LegacyColumnDef,
} from '@tanstack/react-table/legacy'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

export type Column<T> = LegacyColumnDef<any>

export function DataTable<T>({
  columns,
  data,
  onRowClick,
  emptyText = '暂无数据',
}: {
  columns: Column<T>[]
  data: T[]
  onRowClick?: (row: T) => void
  emptyText?: string
}) {
  const cols = useMemo(() => columns, [columns])
  const table = useLegacyTable({
    data: data as never,
    columns: cols as never,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <Table>
      <TableHeader>
        {table.getHeaderGroups().map((hg) => (
          <TableRow key={hg.id}>
            {hg.headers.map((header) => (
              <TableHead key={header.id}>
                {header.isPlaceholder ? null : <FlexRender header={header} />}
              </TableHead>
            ))}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.length === 0 ? (
          <TableRow>
            <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
              {emptyText}
            </TableCell>
          </TableRow>
        ) : (
          table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              className={cn(onRowClick && 'cursor-pointer')}
              onClick={() => onRowClick?.(row.original as T)}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>
                  <FlexRender cell={cell} />
                </TableCell>
              ))}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )
}
