import { useEffect, useState } from 'react'
import { Info, Loader2 } from 'lucide-react'

import { api } from '@/lib/api'
import { explainBody, needsExplanation } from '@/lib/explain'
import type { RouteExplainResult, RouteResult } from '@/lib/types'

type State = { kind: 'loading' } | { kind: 'done'; data: RouteExplainResult } | { kind: 'error' }

// Sentences under the route card, filled by the server from templates with the computed numbers.
// Fetched after the route is shown; on a network error the block is simply hidden.
export default function RouteExplanation({ result }: { result: RouteResult }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const wanted = needsExplanation(result)

  useEffect(() => {
    if (!wanted) return
    const controller = new AbortController()
    api.explainRoute(explainBody(result), controller.signal)
      .then(data => setState({ kind: 'done', data }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [result, wanted])

  if (!wanted || state.kind === 'error') return null
  return (
    <div className="route-explain" aria-label="경로 설명"
      aria-live="polite" data-testid="route-explanation">
      <p className="route-explain-label">
        <Info className="size-3.5" />경로 설명
      </p>
      {state.kind === 'loading' ? (
        <p className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />설명을 만드는 중…
        </p>
      ) : (
        <p>{state.data.text}</p>
      )}
    </div>
  )
}
