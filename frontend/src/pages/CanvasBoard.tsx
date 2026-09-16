import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import '@xyflow/react/dist/style.css'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Connection,
} from '@xyflow/react'
import { canvasNodeTypes } from '../components/canvas/nodes'
import { CanvasEdge } from '../components/canvas/edges/CanvasEdge'
import { CanvasActionContext, type CanvasAction } from '../components/canvas/canvasContext'
import { GeneratePanel } from '../components/canvas/panels/GeneratePanel'
// 方案已改为 ComfyUI 式画布节点（DirectorSchemeNode），不再使用弹窗面板
import { NodeDetailsPanel, type NodeDetailsHandle } from '../components/canvas/panels/NodeDetailsPanel'
import { useCanvasDocument } from '../components/canvas/hooks/useCanvasDocument'
import { useSearchParams } from 'react-router-dom'
import { useConfirm } from '../components/ui/ConfirmDialog'
import { MOCK_VIEWPORT } from '../lib/canvasMock'
import { EDGE_SEMANTICS, type CanvasNodeData, type EdgeSemantic } from '../lib/canvasTypes'
import { cn } from '../lib/cn'
import { api } from '../api/client'
import {
  createBoard,
  enhanceSegmentPrompt,
  generateBoard,
  getBoard,
  listCanvasSchemes,
  importBoardFromSegment,
  importBoardsFromSegments,
  listProjectSegments,
  listProjects,
  auditBoard,
  updateSegmentCompositionPoint,
  type CanvasAudit,
  type ProjectLite,
  type SchemeMeta,
  type SegmentLite,
} from '../api/canvas'

const NODE_COLORS: Record<string, string> = {
  shot: '#7c3aed',
  asset: '#0ea5e9',
  storyboard: '#f59e0b',
  video: '#0f172a',
  note: '#facc15',
  director: '#0f766e',
}

const TOOL_NODES: Array<{ type: string; label: string }> = [
  { type: 'shot', label: '分镜' },
  { type: 'asset', label: '资产' },
  { type: 'storyboard', label: '节拍卡' },
  { type: 'video', label: '视频' },
  { type: 'group', label: '分组' },
  { type: 'note', label: '便签' },
  { type: 'director', label: '方案' },
]

const BOARD_KEY = 'canvas-board-id-v1'

interface GenTaskRunning {
  taskId: string
  nodeIds: string[]
}

function BoardInner() {
  const [boardId, setBoardIdState] = useState<string | null>(() => localStorage.getItem(BOARD_KEY))
  const [remote, setRemote] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [genTask, setGenTask] = useState<GenTaskRunning | null>(null)
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    patchNode,
    deleteNodes,
    selectVersion,
    expandH3,
    resetDemo,
    applyDocument,
    boardLoading,
    boardError,
  } = useCanvasDocument({ boardId })

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [edgeSemantic, setEdgeSemantic] = useState<EdgeSemantic>('sequence')

  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId])

  const showNotice = useCallback((msg: string) => {
    setNotice(msg)
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => setNotice(null), 4500)
  }, [])

  // 首次挂载:无 boardId 时自动建板并播种演示文档(后端不可用则离线演示)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let bid = localStorage.getItem(BOARD_KEY)
      if (!bid) {
        try {
          const created = await createBoard({ name: '导演画布' })
          bid = created.id
          localStorage.setItem(BOARD_KEY, bid)
          // 2026-09:不再播种演示/占位文档，画布保持空状态（空态提示由 CanvasBoard 展示）
        } catch {
          if (!cancelled) showNotice('后端不可用 → 离线演示模式(数据仅存本地)')
          return
        }
      }
      if (!cancelled) {
        setBoardIdState(bid)
        setRemote(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handleConnect = useCallback(
    (conn: Connection) => onConnect(conn, edgeSemantic),
    [onConnect, edgeSemantic],
  )

  // 真实生成:轮询父任务 → 终态后拉取文档(生成结果已由 worker 回写)
  useEffect(() => {
    if (!genTask || !boardId) return
    let stopped = false
    const iv = setInterval(async () => {
      try {
        const t = await api.get<{ status: string; progress: number }>('/tasks/' + genTask.taskId)
        const st: CanvasNodeData['status'] =
          t.status === 'succeeded'
            ? 'done'
            : t.status === 'failed' || t.status === 'cancelled'
              ? 'failed'
              : t.status === 'pending'
                ? 'queued'
                : 'running'
        for (const nid of genTask.nodeIds) {
          patchNode(nid, { status: st, progress: t.progress })
        }
        if (st === 'done' || st === 'failed') {
          clearInterval(iv)
          stopped = true
          try {
            const dto = await getBoard(boardId)
            applyDocument(dto.document)
          } catch {
            /* 文档拉取失败:节点状态已就地更新 */
          }
          if (st === 'failed') showNotice('画布生成任务失败(节点已标记,右侧详情可见原因)')
          setGenTask(null)
        }
      } catch {
        /* 轮询瞬断,下一拍重试 */
      }
    }, 2500)
    return () => {
      if (!stopped) clearInterval(iv)
    }
  }, [genTask, boardId, patchNode, applyDocument, showNotice])

  const handleAction = useCallback(
    (a: CanvasAction) => {
      setSelectedId(a.nodeId)
      const node = nodes.find((n) => n.id === a.nodeId)
      const sid = node?.data.segmentId
      const isRealSegment =
        !!sid &&
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sid)
      const generateReal = (kind: 'image' | 'video') => {
        generateBoard(boardId as string, { node_ids: [a.nodeId], kind })
          .then((res) => {
            const errs = res.nodes.filter((n) => n.error)
            if (errs.length) showNotice(errs.map((n) => n.error).join('; '))
            const okIds = res.nodes.filter((n) => !n.error).map((n) => n.node_id)
            if (okIds.length) setGenTask({ taskId: res.task_id, nodeIds: okIds })
          })
          .catch((e: unknown) => showNotice(String(e)))
      }
      const rejectFake = (kind: 'image' | 'video') => {
        if (node?.type !== 'shot') {
          showNotice('只有「分镜」节点能生成真实' + (kind === 'video' ? '视频' : '图片') + ';请先在画布「从分镜导入」真实分镜')
        } else if (sid && !isRealSegment) {
          showNotice('该分镜节点未绑定真实分镜(segmentId 无效);请删除后重新「从分镜导入」')
        } else {
          showNotice('该节点不是真实分镜,无法生成;请先在画布「从分镜导入」把真实分镜放上画布再点生成')
        }
      }
      if (a.action === 'generate-image') {
        if (node?.data.segmentId && boardId && isRealSegment) generateReal('image')
        else rejectFake('image')
      } else if (a.action === 'generate-video') {
        if (node?.data.segmentId && boardId && isRealSegment) generateReal('video')
        else rejectFake('video')
      } else if (a.action === 'expand-h3') {
        if (sid && boardId && isRealSegment) {
          enhanceSegmentPrompt(sid, node?.data.prompt ?? '', 'image')
            .then((res) => {
              patchNode(a.nodeId, { prompt: res.enhanced_prompt })
              showNotice('扩写完成,已写入节点提示词')
            })
            .catch((e: unknown) => showNotice('扩写失败:' + String(e)))
        } else {
          showNotice('AI 扩写需要真实分镜节点;请先在画布「从分镜导入」真实分镜')
        }
      }
    },
    [nodes, boardId, expandH3, showNotice, patchNode],
  )

  // 面板"AI 扩写"按钮 → 发送节点级扩写动作
  const handleActionExpand = useCallback((nodeId: string) => {
    handleAction({ nodeId, action: 'expand-h3' })
  }, [handleAction])

  const handleAddNode = (type: string) => {
    addNode(type)
  }

  const handleReset = () => {
    // 联机保护：重置会用 mock 节点覆盖整板，若自动保存会把 mock 写回后端画布（2026-08-30 体检）
    if (boardId) {
      showNotice('联机画布已锁定「重置示例」（避免 mock 覆盖真实画布）；离线模式才可用')
      return
    }
    resetDemo()
    showNotice('已重置为示例文档')
  }

  // 分镜导入选择器状态
  const [projects, setProjects] = useState<ProjectLite[]>([])
  const [projectId, setProjectId] = useState('')
  const [segments, setSegments] = useState<SegmentLite[]>([])
  const [selSegs, setSelSegs] = useState<string[]>([])
  const toggleSeg = (id: string) =>
    setSelSegs((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const openImport = useCallback(async () => {
    setImportOpen(true)
    try {
      const ps = await listProjects()
      setProjects(ps)
      setProjectId(ps[0]?.id ?? '')
    } catch (e) {
      showNotice('读取项目失败:' + String(e))
    }
  }, [showNotice])

  useEffect(() => {
    if (!importOpen || !projectId) return
    listProjectSegments(projectId)
      .then(setSegments)
      .catch((e) => showNotice('读取分镜失败:' + String(e)))
  }, [importOpen, projectId, showNotice])

  const doImport = useCallback(
    async (segmentId: string) => {
      try {
        const dto = await importBoardFromSegment(segmentId)
        localStorage.setItem(BOARD_KEY, dto.id)
        setBoardIdState(dto.id)
        setRemote(true)
        applyDocument(dto.document)
        setImportOpen(false)
        showNotice('已导入分镜画布(节点已绑定真实分镜,生成走后端任务)')
      } catch (e) {
        showNotice('导入失败:' + String(e))
      }
    },
    [applyDocument, showNotice],
  )

  // 合并导入多个分镜(整幕/整集上画布)
  const doImportMany = useCallback(async () => {
    if (!selSegs.length) return
    try {
      const dto = await importBoardsFromSegments(selSegs)
      localStorage.setItem(BOARD_KEY, dto.id)
      setBoardIdState(dto.id)
      setRemote(true)
      applyDocument(dto.document)
      setImportOpen(false)
      setSelSegs([])
      showNotice(`已合并导入 ${selSegs.length} 个分镜(幕分组 + 衔接边就绪,节点已绑定)`)
    } catch (e) {
      showNotice('导入失败:' + String(e))
    }
  }, [selSegs, applyDocument, showNotice])

  const toggleAllSegs = () =>
    setSelSegs((prev) => (prev.length === segments.length ? [] : segments.map((s) => s.id)))

  // ---- 导演台模式(2026-08-29):多段连续生视频(内部状态 + 初始行定义见 seqOrder 之后)----
  // 方案即画布节点（2026-08-30）：选择 director 系列方案 → 直接创建 ComfyUI 式方案节点，控件就在节点上
  const [schemes, setSchemes] = useState<SchemeMeta[]>([])
  const [schemeOpen, setSchemeOpen] = useState(false)
  const [expandedScheme, setExpandedScheme] = useState<string | null>(null)
  useEffect(() => {
    if (!remote) return
    listCanvasSchemes().then(setSchemes).catch(() => {})
  }, [remote])

  // 方案节点任务完成 → 重拉画布文档（后端已将整片 video 节点/分镜标记回写）
  useEffect(() => {
    const handler = () => {
      if (!boardId) return
      getBoard(boardId).then((dto) => applyDocument(dto.document)).catch(() => {})
    }
    window.addEventListener('canvas:refresh', handler as EventListener)
    return () => window.removeEventListener('canvas:refresh', handler as EventListener)
  }, [boardId, applyDocument])
  // ---- 序列预览(M3 导演层)----
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewIdx, setPreviewIdx] = useState(0)
  const [previewPlaying, setPreviewPlaying] = useState(false)
  const seqOrder = useMemo(() => {
    const next = new Map<string, string[]>()
    for (const e of edges) {
      if (e.data?.semantic === 'sequence' && e.source && e.target) {
        next.set(e.source, [...(next.get(e.source) ?? []), e.target])
      }
    }
    const indeg = new Set(edges.filter((e) => e.data?.semantic === 'sequence').map((e) => e.target))
    const shots = nodes.filter((n) => n.type === 'shot')
    const out: string[] = []
    const walk = (id: string) => {
      if (!out.includes(id)) {
        out.push(id)
        for (const t of next.get(id) ?? []) walk(t)
      }
    }
    for (const s of shots) if (!indeg.has(s.id)) walk(s.id)
    for (const s of shots) if (!out.includes(s.id)) out.push(s.id)
    return out
  }, [edges, nodes])
  useEffect(() => {
    if (!previewPlaying || !seqOrder.length) return
    const t = setInterval(() => setPreviewIdx((i) => (i + 1 >= seqOrder.length ? 0 : i + 1)), 1800)
    return () => clearInterval(t)
  }, [previewPlaying, seqOrder.length])


  // 导演台初始行:沿 sequence 边排序的真实分镜节点(prompt/时长预填,可增删/排序)
  const directorInitial = useMemo(() => {
    const shots = nodes.filter((n) => n.type === 'shot')
    const byId = new Map(shots.map((n) => [n.id, n] as const))
    const ordered = seqOrder.map((id) => byId.get(id)).filter((x): x is NonNullable<typeof x> => !!x && !!x.data.segmentId)
    if (!ordered.length) return shots.filter((n) => !!n.data.segmentId)
    return ordered
  }, [nodes, seqOrder])

  // ---- 画布体检/评审(M3,替代 P5 camera_plan 数据面)----
  const [auditOpen, setAuditOpen] = useState(false)
  const [audit, setAudit] = useState<CanvasAudit | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)
  const openAudit = useCallback(async () => {
    if (!boardId) {
      showNotice('当前画布未联机,无法体检')
      return
    }
    setAuditLoading(true)
    try {
      setAudit(await auditBoard(boardId))
      setAuditOpen(true)
    } catch (e) {
      showNotice('体检失败:' + String(e))
    } finally {
      setAuditLoading(false)
    }
  }, [boardId, showNotice])

  const ISSUE_META: Record<string, { label: string; cls: string }> = {
    failed: { label: '生成失败', cls: 'bg-red-50 text-red-600 border-red-200' },
    unbound: { label: '未绑定', cls: 'bg-amber-50 text-amber-600 border-amber-200' },
    no_product: { label: '无产物', cls: 'bg-slate-50 text-slate-500 border-slate-200' },
    no_point: { label: '未标点位', cls: 'bg-violet-50 text-violet-600 border-violet-200' },
  }

  const exportSeq = () => {
    const md = seqOrder.map((sid, i) => {
      const n = nodes.find((x) => x.id === sid)
      const d = n?.data
      const last = d?.versions && d.versions.length ? d.versions[d.versions.length - 1] : undefined
      return (
        '### ' + (i + 1) + '. ' + (d?.label ?? '分镜') + (d?.shotType ? ' (' + d.shotType + ')' : '') + '\n' +
        '- 描述: ' + (d?.description ?? '—') + '\n' +
        '- 点位: ' + (d?.compositionPoint ?? '未标') + '\n' +
        '- 关键帧: ' + (last?.url ?? '未生成') + '\n' +
        '- 视频: ' + (d?.videoUrl ?? '未生成') + '\n'
      )
    }).join('\n')
    const blob = new Blob(['# 序列预览 ' + new Date().toISOString().slice(0, 10) + '\n\n' + md], {
      type: 'text/markdown',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'canvas-sequence.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  const confirmDelete = useConfirm()
  const panelRef = useRef<NodeDetailsHandle>(null)
  const handleDeleteNode = async (id: string) => {
    if (await confirmDelete({
      title: '删除节点',
      message: '确定删除该节点及相连连线吗?画布删除不会影响后端分镜数据。',
      danger: true,
    })) {
      deleteNodes([id])
      if (selected?.id === id) setSelectedId(null)
      showNotice('节点已删除(自动保存中)')
    }
  }

  // ---- 剪辑器跳转锚点:?board=&node= ----
  const [searchParams] = useSearchParams()
  const appliedJump = useRef(false)
  useEffect(() => {
    if (appliedJump.current) return
    const board = searchParams.get('board')
    const node = searchParams.get('node')
    if (!board || !node) return
    if (boardId !== board) {
      getBoard(board)
        .then((dto) => {
          localStorage.setItem(BOARD_KEY, dto.id)
          setBoardIdState(dto.id)
          setRemote(true)
          applyDocument(dto.document)
          setSelectedId(node)
        })
        .catch(() => showNotice('画布跳转失败:可能会话已过期,请刷新页面重试'))
      appliedJump.current = true
    } else if (boardId === board) {
      setSelectedId(node)
      appliedJump.current = true
    }
  }, [boardId, searchParams, applyDocument])

  // ---- 节点补丁包装:点位变动时同步回写分镜 ----
  const handlePatchNode = useCallback(
    (id: string, patch: Partial<CanvasNodeData>) => {
      patchNode(id, patch)
      const node = nodes.find((n) => n.id === id)
      if (node?.data.segmentId && patch.compositionPoint !== undefined) {
        updateSegmentCompositionPoint(node.data.segmentId, String(patch.compositionPoint)).catch(() => {})
      }
    },
    [patchNode, nodes],
  )

  return (
    <CanvasActionContext.Provider value={handleAction}>
      <div className="relative h-[calc(100vh-56px)] w-full">
        {boardLoading && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-white/60 backdrop-blur-sm">
            <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow">正在加载画布…</div>
          </div>
        )}
        {!boardLoading && boardError && (
          <div className="pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2">
            <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-700 shadow">
              <span>⚠️ 画布加载失败：{boardError}（已回退本地/演示文档）</span>
            </div>
          </div>
        )}
        {!boardLoading && !boardError && nodes.length === 0 && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <div className="rounded-xl border border-dashed border-slate-300 bg-white/80 px-5 py-4 text-center text-sm text-slate-400">
              画布为空。可从左侧工具栏拖入节点，或<a className="text-brand-600 underline" href="#">从分镜导入</a>整集分镜上画布。
            </div>
          </div>
        )}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={handleConnect}
          nodeTypes={canvasNodeTypes}
          edgeTypes={{ canvas: CanvasEdge }}
          defaultEdgeOptions={{ type: 'canvas' }}
          defaultViewport={MOCK_VIEWPORT}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          deleteKeyCode={['Backspace', 'Delete']}
          onNodesDelete={() => setSelectedId(null)}
          minZoom={0.25}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color="#cbd5e1" />
          <Controls position="bottom-left" showInteractive={false} />
          <MiniMap
            position="bottom-right"
            pannable
            zoomable
            nodeColor={(n) => NODE_COLORS[n.type ?? ''] ?? '#94a3b8'}
            nodeStrokeWidth={2}
            maskColor="rgba(255,255,255,0.62)"
          />
        </ReactFlow>

        {/* 左侧固定工具栏（竖排） */}
        <div className="pointer-events-none absolute left-3 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center gap-2">
          <div className="pointer-events-auto flex w-[124px] flex-col items-stretch gap-1 rounded-xl border border-slate-200 bg-white/95 p-1.5 shadow-lg backdrop-blur">
            <div className="flex w-full items-center justify-between px-0.5">
              <span className="text-[10px] font-semibold text-slate-600">
                导演画布<em className="ml-1 not-italic text-[8px] text-slate-400">M1</em>
              </span>
              <span
                className={cn(
                  'rounded-full px-1.5 py-0.5 text-[9px] font-medium',
                  remote ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600',
                )}
                title={remote ? ('已联机(画布 ' + (boardId ?? '') + ')') : '后端不可用,数据仅存本地'}
              >
                {remote ? '已联机' : '离线'}
              </span>
            </div>
            <span className="h-px w-full bg-slate-200" />
            {TOOL_NODES.map((t) => (
              <button
                key={t.type}
                onClick={() => handleAddNode(t.type)}
                className="rounded-md border border-slate-200 px-1.5 py-1 text-[9px] font-medium text-slate-600 hover:bg-slate-50"
              >
                + {t.label}
              </button>
            ))}
            <span className="h-px w-full bg-slate-200" />
            {(Object.keys(EDGE_SEMANTICS) as EdgeSemantic[]).map((sem) => (
              <button
                key={sem}
                onClick={() => setEdgeSemantic(sem)}
                className={cn(
                  'flex items-center gap-1 rounded-md border px-1.5 py-1 text-[9px] font-medium',
                  edgeSemantic === sem
                    ? 'border-slate-400 bg-slate-100 text-slate-800'
                    : 'border-slate-200 text-slate-500 hover:bg-slate-50',
                )}
              >
                <span
                  className="h-1.5 w-3 rounded-full"
                  style={{ background: EDGE_SEMANTICS[sem].color, opacity: 0.85 }}
                />
                {EDGE_SEMANTICS[sem].label}
              </button>
            ))}
            <span className="h-px w-full bg-slate-200" />
            <button
              onClick={openImport}
              className="rounded-md border border-brand-200 bg-brand-50 px-1.5 py-1 text-[9px] font-medium text-brand-600 hover:bg-brand-100"
            >
              从分镜导入
            </button>
            <button
              onClick={() => { setPreviewIdx(0); setPreviewPlaying(false); setPreviewOpen(true) }}
              disabled={!seqOrder.length}
              className="rounded-md border border-slate-200 px-1.5 py-1 text-[9px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              title="沿 sequence 连线按顺序预览分镜"
            >
              ▶ 序列预览
            </button>
            <div className="relative">
              <button
                onClick={() => setSchemeOpen((o) => !o)}
                disabled={!remote || !schemes.length}
                className="rounded-md border border-brand-200 bg-brand-50 px-1.5 py-1 text-[9px] font-medium text-brand-600 hover:bg-brand-100 disabled:opacity-40"
                title="选择生成方案：单镜直出 / 多段连拍·整片(可扩展)"
              >
                🎬 方案{schemes.length ? `(${schemes.length})` : ''} ▾
              </button>
              {schemeOpen && (
                <div className="absolute left-full top-0 z-30 ml-1 w-72 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
                  <div className="border-b border-slate-100 bg-slate-50 px-2 py-1 text-[9px] font-semibold text-slate-400">
                    生成方案（可插拔注册）
                  </div>
                  {schemes.map((s) => (
                    <div key={s.key} className="border-b border-slate-100 last:border-0">
                      <div className="flex items-center px-2.5 py-1.5 hover:bg-slate-50">
                        <button
                          onClick={() => {
                            setSchemeOpen(false)
                            if (s.input_kind === 'director-segments') {
                              if (!directorInitial.length) { showNotice('方案「' + s.label + '」需要先「从分镜导入」至少一个真实分镜'); return }
                              addNode('director', {
                                data: {
                                  schemeKey: s.key,
                                  schemeLabel: s.label,
                                  taskType: s.key === 'director_text' ? 't2v' : 'r2v',
                                  shots: directorInitial.map((n) => ({
                                    node_id: n.id,
                                    label: n.data.label ?? '分镜',
                                    prompt: (n.data.prompt ?? n.data.description ?? '').slice(0, 300),
                                    duration_sec: n.data.durationSec ?? 5,
                                    from_prev: true,
                                  })),
                                  ratio: '16:9', res: '0.7mp', contextEnabled: true, contextFrames: 22, status: 'idle',
                                },
                                position: { x: 560, y: 60 },
                              })
                              setSelectedId(directorInitial[0]?.id ?? null)
                              showNotice('已创建方案节点「' + s.label + '」到画布 —— 控件直接在节点上编辑与执行')
                            } else if (s.input_kind === 'node-batch') {
                              showNotice('方案「' + s.label + '」：选中分镜后，右栏「生成面板」逐镜出关键帧/视频')
                            } else {
                              showNotice('方案「' + s.label + '」暂未提供输入面板')
                            }
                          }}
                          className="min-w-0 flex-1 text-left"
                        >
                          <span className="block text-[11px] font-semibold text-slate-700">{s.label}</span>
                          <span className="block truncate text-[9px] leading-snug text-slate-400">{s.description}</span>
                        </button>
                        <button
                          onClick={() => setExpandedScheme(expandedScheme === s.key ? null : s.key)}
                          className="shrink-0 px-1 text-[11px] text-slate-400 hover:text-slate-600"
                          title="方案详情"
                        >
                          {expandedScheme === s.key ? '▲' : 'ℹ️'}
                        </button>
                      </div>
                      {expandedScheme === s.key && (
                        <div className="mx-2.5 mb-2 rounded-md border border-slate-100 bg-slate-50/70 px-2 py-1.5 text-[9px] leading-relaxed text-slate-500">
                          <div className="pb-0.5 font-semibold text-slate-600">{s.label}</div>
                          <div>{s.description}</div>
                          <div className="pt-1 text-slate-400">输入类型：{s.input_kind === 'director-segments' ? '分段脚本表格（多段连拍）' : s.input_kind} · 执行：后端按方案注册表分发；产物按方案协议回写画布</div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <button
              onClick={openAudit}
              disabled={auditLoading}
              className="rounded-md border border-slate-200 px-1.5 py-1 text-[9px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              title="体检/评审:失败、未绑定、无产物、未标点位、任务状态"
            >
              {auditLoading ? '体检中…' : '🧭 体检'}
            </button>
            {genTask && (
              <span className="flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[9px] font-medium text-amber-700">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                生成中 {genTask.nodeIds.length} 镜
              </span>
            )}
            <button
              onClick={handleReset}
              className="rounded-md px-2 py-1 text-[10px] text-slate-400 hover:text-slate-600"
            >
              重置示例
            </button>
          </div>
          {remote && (
            <div className="pointer-events-auto rounded-full bg-white/85 px-2 py-0.5 text-[9px] text-slate-400 shadow">
              自动保存到后端 · 未绑定分镜的节点仍为本地演示
            </div>
          )}
        </div>

        {/* 连线图例(底部居中,避开左侧工具栏) */}
        <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-2 shadow">
          {(Object.keys(EDGE_SEMANTICS) as EdgeSemantic[]).map((sem) => (
            <div key={sem} className="flex items-center gap-1.5 py-0.5 text-[9px] text-slate-500">
              <span
                className="h-0.5 w-6"
                style={{
                  background: EDGE_SEMANTICS[sem].color,
                  opacity: 0.85,
                  ...(EDGE_SEMANTICS[sem].dashed
                    ? {
                        backgroundImage:
                          'linear-gradient(90deg, transparent 40%, #94a3b8 40%, #94a3b8 60%, transparent 60%)',
                      }
                    : {}),
                }}
              />
              {EDGE_SEMANTICS[sem].label}
            </div>
          ))}
        </div>

        {/* 右侧详情 + 生成面板 */}
        {selected && (
          <aside className="absolute inset-y-2 right-2 z-20 w-[360px] overflow-y-auto rounded-xl border border-slate-200 bg-white/95 shadow-xl backdrop-blur">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-100 bg-white/95 px-3 py-2">
              <span className="truncate text-[12px] font-semibold text-slate-700">
                {selected.data.label ?? '未命名节点'}
              </span>
              <button onClick={() => setSelectedId(null)} className="text-slate-400 hover:text-slate-600" aria-label="关闭">
                ✕
              </button>
            </div>
            <NodeDetailsPanel ref={panelRef} node={selected} onPatch={handlePatchNode} />
            <GeneratePanel onExpand={handleActionExpand} 
              node={selected}
              onGenerate={(id, kind) => {
                if (kind === 'image') handleAction({ nodeId: id, action: 'generate-image' })
                else handleAction({ nodeId: id, action: 'generate-video' })
              }}
              onSelectVersion={selectVersion}
            />

            {/* 底部操作栏:一行两按钮(保存修改 | 删除节点) */}
            <div className="sticky bottom-0 flex items-center gap-2 border-t border-slate-100 bg-white/95 px-3 py-2.5">
              <button
                onClick={() => panelRef.current?.save()}
                className="flex-1 rounded-lg border border-slate-300 py-2 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
              >
                保存修改
              </button>
              <button
                onClick={() => handleDeleteNode(selected.id)}
                className="flex-1 rounded-lg border border-red-300 py-2 text-[11px] font-medium text-red-500 hover:bg-red-50"
              >
                删除节点
              </button>
            </div>
          </aside>
        )}

        {/* 方案已改为 ComfyUI 式画布节点(2026-08-30)：director 节点自带全部控件与产物预览，无需任何弹窗/dock */}

        {/* 分镜导入选择器 */}
        {importOpen && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={() => setImportOpen(false)}>
            <div
              className="w-[520px] max-h-[70vh] overflow-y-auto rounded-xl border border-slate-200 bg-white p-4 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-[13px] font-semibold text-slate-700">从分镜导入画布</h3>
                <button onClick={() => setImportOpen(false)} className="text-slate-400 hover:text-slate-600" aria-label="关闭">
                  ✕
                </button>
              </div>
              <label className="block pb-1 text-[10px] font-medium text-slate-400">项目</label>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="mb-3 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[11px]"
              >
                {projects.length === 0 && <option value="">加载项目…</option>}
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </select>
              <label className="block pb-1 text-[10px] font-medium text-slate-400">
                分镜(可多选合并导入:整幕/整集上画布)
                <button
                  type="button"
                  onClick={toggleAllSegs}
                  className="ml-2 rounded border border-slate-200 px-1.5 py-0.5 text-[9px] text-slate-500 hover:bg-slate-50"
                >
                  {segments.length && selSegs.length === segments.length ? '清空' : '全选'}
                </button>
              </label>
              <div className="max-h-56 space-y-1 overflow-y-auto">
                {segments.length === 0 && (
                  <p className="py-3 text-center text-[11px] text-slate-400">该项目暂无分镜</p>
                )}
                {segments.slice(0, 40).map((s) => (
                  <div
                    key={s.id}
                    className={
                      'flex items-center gap-2 rounded-lg border px-2.5 py-1.5 ' +
                      (selSegs.includes(s.id) ? 'border-brand-300 bg-brand-50/50' : 'border-slate-200')
                    }
                  >
                    <input
                      type="checkbox"
                      checked={selSegs.includes(s.id)}
                      onChange={() => toggleSeg(s.id)}
                      className="accent-brand-500"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="text-[11px] font-semibold text-slate-700">第 {s.index} 镜</span>
                      <span className="ml-2 text-[10px] text-slate-400">
                        {s.shot_type ?? ''} · {(s.description ?? '').slice(0, 40) || '无描述'}
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={() => doImport(s.id)}
                      className="shrink-0 rounded px-1.5 py-0.5 text-[9px] text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                      title="仅导入这一个分镜"
                    >
                      单镜
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                disabled={!selSegs.length}
                onClick={doImportMany}
                className="mt-2 w-full rounded-lg bg-brand-500 px-2 py-1.5 text-[11px] font-semibold text-white hover:bg-brand-600 disabled:opacity-40"
              >
                合并导入所选 ({selSegs.length} 个)
              </button>
            </div>
          </div>
        )}

        {/* 序列预览弹窗(M3 导演层) */}
        {previewOpen && seqOrder.length > 0 && (
          <div
            className="absolute inset-0 z-30 flex items-center justify-center bg-slate-900/30"
            onClick={() => { setPreviewOpen(false); setPreviewPlaying(false) }}
          >
            <div
              className="w-[560px] max-w-[94%] rounded-2xl border border-slate-200 bg-white shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                <span className="text-[12px] font-semibold text-slate-700">
                  序列预览 <span className="ml-1 text-[10px] text-slate-400">{previewIdx + 1} / {seqOrder.length}</span>
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setPreviewPlaying((p) => !p)}
                    className="rounded-md border border-slate-200 px-1.5 py-1 text-[9px] font-medium text-slate-600 hover:bg-slate-50"
                  >
                    {previewPlaying ? '❚❚ 暂停' : '▶ 播放'}
                  </button>
                  <button
                    onClick={exportSeq}
                    className="rounded-md border border-slate-200 px-1.5 py-1 text-[9px] font-medium text-slate-600 hover:bg-slate-50"
                    title="导出 Markdown 走查清单"
                  >
                    ⬇ 导出
                  </button>
                  <button
                    onClick={() => { setPreviewOpen(false); setPreviewPlaying(false) }}
                    className="rounded-md px-2 py-1 text-[10px] text-slate-400 hover:text-slate-600"
                  >
                    ✕
                  </button>
                </div>
              </div>
              {(() => {
                const cur = nodes.find((n) => n.id === seqOrder[previewIdx])
                if (!cur) return null
                const d = cur.data
                const last = d.versions && d.versions.length ? d.versions[d.versions.length - 1] : undefined
                const img = last?.url ?? d.imageUrl ?? (d.videoUrl ? undefined : '')
                return (
                  <div className="px-4 py-3">
                    {img ? (
                      <img src={img} alt="" className="mx-auto h-52 rounded-xl border border-slate-200 object-cover shadow-sm" />
                    ) : d.videoUrl ? (
                      <video src={d.videoUrl} controls className="mx-auto h-52 rounded-xl border border-slate-200 bg-black object-contain" />
                    ) : (
                      <div className="mx-auto grid h-52 w-full place-items-center rounded-xl bg-slate-100 text-[11px] text-slate-400">
                        第 {d.label ?? '?'} 镜 · 未生成产物
                      </div>
                    )}
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <span className="text-[11px] font-semibold text-slate-700">{d.label ?? '分镜'}</span>
                      <span className="text-[10px] text-slate-400">{d.shotType ?? ''}{d.compositionPoint ? ' · ◉ ' + (d.compositionPoint ?? '') : ''}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-slate-500">{d.description ?? ''}</p>
                    <div className="mt-2 flex items-center justify-between">
                      <button
                        onClick={() => { setPreviewPlaying(false); setPreviewIdx((i) => (i - 1 + seqOrder.length) % seqOrder.length) }}
                        className="rounded-md border border-slate-200 px-2 py-1 text-[10px] text-slate-500 hover:bg-slate-50"
                      >
                        ← 上一镜
                      </button>
                      <span className="text-[9px] text-slate-400">sequence 连线顺序</span>
                      <button
                        onClick={() => { setPreviewPlaying(false); setPreviewIdx((i) => (i + 1) % seqOrder.length) }}
                        className="rounded-md border border-slate-200 px-2 py-1 text-[10px] text-slate-500 hover:bg-slate-50"
                      >
                        下一镜 →
                      </button>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1 border-t border-slate-100 pt-2">
                      {seqOrder.map((sid, i) => {
                        const n = nodes.find((x) => x.id === sid)
                        return (
                          <button
                            key={sid}
                            onClick={() => { setPreviewPlaying(false); setPreviewIdx(i) }}
                            className={
                              'rounded px-1.5 py-0.5 text-[9px] ' +
                              (i === previewIdx ? 'bg-brand-500 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200')
                            }
                          >
                            {n?.data.label ?? '镜'}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )
              })()}
            </div>
          </div>
        )}

        {/* 画布体检弹窗 */}
        {auditOpen && audit && (
          <div
            className="absolute inset-0 z-30 flex items-center justify-center bg-slate-900/30"
            onClick={() => setAuditOpen(false)}
          >
            <div
              className="w-[520px] max-w-[94%] rounded-2xl border border-slate-200 bg-white shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                <span className="text-[12px] font-semibold text-slate-700">
                  画布体检{audit.items.length > 0 && (
                    <span className="ml-1 text-[10px] text-slate-400">共 {audit.items.length} 项</span>
                  )}
                </span>
                <button
                  onClick={() => setAuditOpen(false)}
                  className="rounded-md px-2 py-1 text-[10px] text-slate-400 hover:text-slate-600"
                >
                  ✕
                </button>
              </div>
              <div className="max-h-[60vh] space-y-1.5 overflow-y-auto px-4 py-3">
                {audit.items.length === 0 && (
                  <p className="py-6 text-center text-[11px] text-slate-400">无问题,画布健康 ✅</p>
                )}
                {audit.items.map((it, i) => {
                  const meta = ISSUE_META[it.issue] ?? ISSUE_META.no_product
                  return (
                    <button
                      key={i}
                      onClick={() => { setSelectedId(it.node_id); setAuditOpen(false) }}
                      className="flex w-full items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 text-left hover:border-brand-300"
                      title={it.detail ?? ''}
                    >
                      <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-medium', meta.cls)}>
                        {meta.label}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] text-slate-700">{it.label}</span>
                      <span className="shrink-0 text-[9px] text-slate-400">定位 →</span>
                    </button>
                  )
                })}
                <div className="mt-2 space-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-500">
                  {audit.running_tasks > 0 && (
                    <p className="font-medium text-amber-600">⏳ 运行中批量任务 {audit.running_tasks} 个(节点进度就地更新)</p>
                  )}
                  {audit.failed_tasks > 0 && (
                    <p className="font-medium text-red-500">✗ 失败批量任务 {audit.failed_tasks} 个</p>
                  )}
                  {audit.recent_failed.map((f, i) => (
                    <p key={i} className="line-clamp-1 text-slate-400">  {f.error}</p>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 通知 toast */}
        {notice && (
          <div className="pointer-events-none absolute bottom-6 left-1/2 z-40 -translate-x-1/2">
            <div className="pointer-events-auto rounded-lg border border-slate-200 bg-slate-900 px-3 py-1.5 text-[11px] text-white shadow-xl">
              {notice}
            </div>
          </div>
        )}
      </div>
    </CanvasActionContext.Provider>
  )
}

export default function CanvasBoard() {
  return (
    <ReactFlowProvider>
      <BoardInner />
    </ReactFlowProvider>
  )
}
