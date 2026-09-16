import { Handle, Position } from '@xyflow/react'

/** 节点左右连接柄:左侧 target(入),右侧 source(出) */
export function NodeHandles() {
  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !border-0 !bg-slate-400"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !border-0 !bg-slate-400"
      />
    </>
  )
}
