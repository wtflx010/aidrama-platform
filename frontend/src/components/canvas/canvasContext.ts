import { createContext, useContext } from 'react'

export type CanvasActionId = 'generate-image' | 'generate-video' | 'expand-h3'

export interface CanvasAction {
  nodeId: string
  action: CanvasActionId
}

export const CanvasActionContext = createContext<(a: CanvasAction) => void>(() => {})

export const useCanvasAction = () => useContext(CanvasActionContext)
