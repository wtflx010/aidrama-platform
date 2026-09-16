import { useCallback, useEffect, useRef, useState } from 'react'
import { addEdge, MarkerType, useEdgesState, useNodesState, type Connection, type OnEdgesChange, type OnNodesChange } from '@xyflow/react'
import { STORAGE_KEY } from '../../../lib/canvasMock'
import { versionImageUrl } from '../../../lib/canvasImages'
import { getBoard, saveBoard } from '../../../api/canvas'
import type { CanvasBoardDocument } from '../../../api/canvas'
import {
  EDGE_SEMANTICS,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
  type EdgeSemantic,
} from '../../../lib/canvasTypes'

interface SavedDoc {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
}

function loadSaved(): SavedDoc | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as SavedDoc
    if (Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) return parsed
    return null
  } catch {
    return null
  }
}

const DEFAULTS: Record<string, Pick<CanvasNodeData, 'label' | 'kind'>> = {
  shot: { label: '新分镜' },
  asset: { label: '新资产', kind: 'character' },
  storyboard: { label: '节拍卡' },
  video: { label: '新视频' },
  group: { label: '新分组' },
  note: { label: '便签' },
  director: { label: '新方案' },
}

export interface CanvasDocumentApi {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  onNodesChange: OnNodesChange<CanvasNode>
  onEdgesChange: OnEdgesChange<CanvasEdge>
  onConnect: (conn: Connection, semantic: EdgeSemantic) => void
  addNode: (type: string, overrides?: { data?: Partial<CanvasNodeData>; position?: { x: number; y: number } }) => void
  patchNode: (id: string, patch: Partial<CanvasNodeData>) => void
  deleteNodes: (ids: string[]) => void
  selectVersion: (id: string, index: number) => void
  simulateTask: (nodeId: string, kind: 'image' | 'video') => void
  expandH3: (nodeId: string) => void
  resetDemo: () => void
  applyDocument: (doc: CanvasBoardDocument) => void
  boardLoading: boolean
  boardError: string | null
}

/** M1 画布文档管理:节点/连线状态 + 后端持久化(远端优先,离线回退 localStorage)+ mock 生成。
 *
 * 传 boardId 时:挂载后先拉远端文档并覆盖本地,mock 无绑定分镜节点仍可演示;
 * 保存防抖 1.2s 同步到后端,失败自动回退本地缓存。不传 boardId 时保持纯 M0 本地行为。
 */
export function useCanvasDocument(opts?: { boardId?: string | null }): CanvasDocumentApi {
  const boardId = opts?.boardId ?? null
  const saved = useRef<SavedDoc | null>(loadSaved())
  // 画布默认空（不再播种演示/占位节点）；仅当存在真实保存文档或远端板时加载
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(saved.current?.nodes ?? [])
  const [edges, setEdges, onEdgesChange] = useEdgesState<CanvasEdge>(saved.current?.edges ?? [])
  const nodesRef = useRef<CanvasNode[]>(nodes)
  const progressRef = useRef<Record<string, number>>({})
  const runningRef = useRef<Set<string>>(new Set())
  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([])
  const cascade = useRef(0)
  const seeded = useRef(false)
  const [boardLoading, setBoardLoading] = useState(false)
  const [boardError, setBoardError] = useState<string | null>(null)

  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])

  // 远端优先:挂载后拉取画布文档覆盖本地
  // 远端优先:挂载后拉取画布文档覆盖本地；带 loading/error 态（供空态/加载/错误态 UI）
  useEffect(() => {
    if (!boardId || seeded.current) return
    seeded.current = true
    setBoardLoading(true)
    setBoardError(null)
    getBoard(boardId)
      .then((dto) => {
        if (dto.document?.nodes) {
          setNodes(dto.document.nodes)
          setEdges(dto.document.edges ?? [])
        }
        setBoardLoading(false)
      })
      .catch((err: unknown) => {
        /* 后端不可用:回退本地/演示文档,并标记错误态供 UI 展示 */
        setBoardLoading(false)
        setBoardError((err as Error)?.message || '后端暂不可达')
      })
  }, [boardId, setNodes, setEdges])

  // 防抖自动保存:远端优先,失败回退 localStorage 缓存
  useEffect(() => {
    // 2026-08-30:生成期间锁定自动保存（任一节点 running/queued 即视为生成中）——
    // 防本地整文档 PUT 覆盖 worker 正在回写的产物
    if (nodes.some((n) => n.data?.status === 'running' || n.data?.status === 'queued')) return
    const t = setTimeout(async () => {
      const doc: CanvasBoardDocument = { nodes, edges }
      if (boardId) {
        try {
          await saveBoard(boardId, { document: doc })
        } catch {
          try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ nodes, edges }))
          } catch {
            /* ignore */
          }
        }
        return
      }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ nodes, edges }))
      } catch {
        /* 原型阶段忽略存储异常 */
      }
    }, 1200)
    return () => clearTimeout(t)
  }, [nodes, edges, boardId])

  // 卸载清理定时器
  useEffect(
    () => () => {
      timers.current.forEach((t) => clearTimeout(t))
    },
    [],
  )

  const patchNode = useCallback(
    (id: string, patch: Partial<CanvasNodeData>) => {
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)))
    },
    [setNodes],
  )

  /** 删除节点:同步清除所有与之相连的边(自动保存防抖落库) */
  const deleteNodes = useCallback(
    (ids: string[]) => {
      if (!ids.length) return
      // 2026-08-30:删除分镜时同步清理所有方案节点 data.shots 引用（否则提交必失败）
      setNodes((ns) =>
        ns
          .filter((n) => !ids.includes(n.id))
          .map((n) =>
            n.type === 'director' && Array.isArray(n.data?.shots)
              ? { ...n, data: { ...n.data, shots: n.data.shots.filter((s) => !ids.includes(s.node_id)) } }
              : n,
          ),
      )
      setEdges((es) => es.filter((e) => !(ids.includes(e.source) || ids.includes(e.target))))
    },
    [setNodes, setEdges],
  )

  const selectVersion = useCallback(
    (id: string, index: number) => patchNode(id, { activeVersion: index }),
    [patchNode],
  )

  const onConnect = useCallback(
    (conn: Connection, semantic: EdgeSemantic) => {
      const cfg = EDGE_SEMANTICS[semantic]
      setEdges((es) =>
        addEdge(
          {
            ...conn,
            type: 'canvas',
            data: { semantic, label: cfg.label },
            markerEnd: { type: MarkerType.ArrowClosed, color: cfg.color } as CanvasEdge['markerEnd'],
          },
          es,
        ),
      )
    },
    [setEdges],
  )

  const addNode = useCallback(
    (type: string, overrides?: { data?: Partial<CanvasNodeData>; position?: { x: number; y: number } }) => {
      cascade.current += 1
      const idx = cascade.current
      const node: CanvasNode = {
        id: `n-${Date.now()}-${idx}`,
        type,
        position: overrides?.position ?? { x: 380 + (idx % 3) * 36, y: 60 + Math.floor(idx / 3) * 40 },
        data: { ...DEFAULTS[type], status: 'idle', ...(overrides?.data ?? {}) },
      }
      setNodes((ns) => [...ns, node])
    },
    [setNodes],
  )

  const spawnVideoNode = useCallback(
    (srcId: string) => {
      const src = nodesRef.current.find((n) => n.id === srcId)
      const node: CanvasNode = {
        id: `video-${Date.now()}`,
        type: 'video',
        position: src ? { x: src.position.x + 440, y: src.position.y + 140 } : { x: 1000, y: 80 },
        data: {
          label: `${src?.data.label ?? '镜头'} · 视频`,
          description: '承接/衔接生成视频(演示)',
          durationSec: 5 + Math.round(Math.random() * 6),
          status: 'done',
        },
      }
      setNodes((ns) => [...ns, node])
    },
    [setNodes],
  )

  /** mock 生成(仅用于未绑定后端分镜的节点/离线演示):queued → running → done */
  const simulateTask = useCallback(
    (nodeId: string, kind: 'image' | 'video') => {
      if (runningRef.current.has(nodeId)) return
      runningRef.current.add(nodeId)
      progressRef.current[nodeId] = 0

      setNodes((ns) =>
        ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, status: 'queued', progress: 0 } } : n)),
      )
      timers.current.push(
        setTimeout(() => {
          setNodes((ns) =>
            ns.map((n) =>
              n.id === nodeId ? { ...n, data: { ...n.data, status: 'running', progress: 6 } } : n,
            ),
          )
          const iv = setInterval(() => {
            const cur = progressRef.current[nodeId] ?? 6
            const next = Math.min(100, cur + 8 + Math.round(Math.random() * 14))
            progressRef.current[nodeId] = next
            if (next >= 100) {
              clearInterval(iv)
              runningRef.current.delete(nodeId)
              if (kind === 'image') {
                const src = nodesRef.current.find((n) => n.id === nodeId)
                const versions = src?.data.versions ?? []
                const idx = versions.length
                setNodes((ns) =>
                  ns.map((n) =>
                    n.id === nodeId
                      ? {
                          ...n,
                          data: {
                            ...n.data,
                            status: 'done',
                            progress: 100,
                            versions: [
                              ...versions,
                              {
                                id: `v-${Date.now()}`,
                                url: versionImageUrl(nodeId, idx, n.data.label ?? '关键帧'),
                                createdAt: new Date().toLocaleString('zh-CN'),
                              },
                            ],
                            activeVersion: idx,
                          },
                        }
                      : n,
                  ),
                )
              } else {
                spawnVideoNode(nodeId)
                setNodes((ns) =>
                  ns.map((n) =>
                    n.id === nodeId ? { ...n, data: { ...n.data, status: 'done', progress: 100 } } : n,
                  ),
                )
              }
            } else {
              setNodes((ns) =>
                ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, progress: next } } : n)),
              )
            }
          }, 420)
          timers.current.push(iv)
        }, 600),
      )
    },
    [setNodes, spawnVideoNode],
  )

  const expandH3 = useCallback(
    (nodeId: string) => {
      const n = nodesRef.current.find((x) => x.id === nodeId)
      const desc = n?.data.description ?? '镜头动作描述'
      const skeleton =
        '【H3 提示词骨架】\n' +
        `integrated_multimodal_description: ${desc}\n` +
        'overall_soundscape: 雨声、脚步、环境环境音\n' +
        'non_diegetic_music: 情绪钢琴,低音铺底\n' +
        '(原型:正式版接 h3-prompt-writing 技能/本地 dgx-vllm 生成完整 H3)'
      patchNode(nodeId, { prompt: skeleton })
    },
    [patchNode],
  )

  const applyDocument = useCallback(
    (doc: CanvasBoardDocument) => {
      if (!doc?.nodes) return
      setNodes(doc.nodes)
      setEdges(doc.edges ?? [])
    },
    [setNodes, setEdges],
  )

  const resetDemo = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    cascade.current = 0
    runningRef.current = new Set()
    progressRef.current = {}
    setNodes([])
    setEdges([])
  }, [setNodes, setEdges])

  return {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    patchNode,
    deleteNodes,
    selectVersion,
    simulateTask,
    expandH3,
    resetDemo,
    applyDocument,
    boardLoading,
    boardError,
  }
}
