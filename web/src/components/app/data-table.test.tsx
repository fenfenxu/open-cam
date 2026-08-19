import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DataTable, type Column } from './data-table'

type Row = { id: number; name: string }

const columns: Column<Row>[] = [
  { accessorKey: 'name', header: '名称' },
]

describe('DataTable', () => {
  it('空数据渲染暂无事件', () => {
    render(<DataTable columns={columns} data={[]} emptyText="暂无事件" />)
    expect(screen.getByText('暂无事件')).toBeInTheDocument()
  })
})
