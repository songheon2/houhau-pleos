import type { RerouteResult } from './types'

// The zones of the current route the driver is asked about.
export function avoidIds(decision: RerouteResult): string[] {
  return [...decision.current.hard_zone_ids, ...decision.current.soft_zone_ids]
}

export function avoidCount(decision: RerouteResult): number {
  return avoidIds(decision).length
}

// What is read aloud while driving: two short sentences from the computed numbers, not the
// longer reason on the card (which stays on screen). "비가 강해졌어요" was already spoken.
export function spokenRerouteSummary(decision: RerouteResult): string {
  const before = avoidCount(decision)
  const after = decision.candidate.hard_zone_ids.length + decision.candidate.soft_zone_ids.length
  const hardBefore = decision.current.hard_zone_ids.length
  const hardAfter = decision.candidate.hard_zone_ids.length
  // A change can be asked for when only the must-avoid zones drop, so the total may not fall.
  const zones = after === 0
    ? `바꾸면 피할 구간 ${before}곳을 모두 지나지 않아요.`
    : after < before
      ? `바꾸면 피할 구간이 ${before}곳에서 ${after}곳으로 줄어요.`
      : `바꾸면 반드시 피할 구간이 ${hardBefore}곳에서 ${hardAfter}곳으로 줄어요.`
  const minutes = Math.round((decision.candidate.duration_s - decision.current.duration_s) / 60)
  const time = minutes > 0 ? `${minutes}분 더 걸려요.` : minutes < 0 ? `${-minutes}분 덜 걸려요.` : '시간은 거의 같아요.'
  return `${zones} ${time}`
}
