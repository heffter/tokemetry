// Machine liveness status from a last-seen timestamp. Thresholds live here
// once (not split across two disagreeing functions) and are evaluated against
// a caller-supplied "now" so the status can tick without going stale.

export type MachineLevel = 'online' | 'recent' | 'stale' | 'offline';

export interface MachineStatus {
  level: MachineLevel;
  /** Human "time ago" string. */
  ago: string;
}

const MINUTE = 60_000;

export function machineStatus(
  lastSeen: string | null,
  nowMs: number
): MachineStatus {
  if (lastSeen === null) return { level: 'offline', ago: 'never' };
  const elapsedMin = Math.max(
    0,
    (nowMs - new Date(lastSeen).getTime()) / MINUTE
  );

  let level: MachineLevel;
  if (elapsedMin < 2) level = 'online';
  else if (elapsedMin < 30) level = 'recent';
  else if (elapsedMin < 1440) level = 'stale';
  else level = 'offline';

  let ago: string;
  if (elapsedMin < 1) ago = 'just now';
  else if (elapsedMin < 60) ago = `${Math.round(elapsedMin)}m ago`;
  else if (elapsedMin < 1440) ago = `${Math.floor(elapsedMin / 60)}h ago`;
  else ago = `${Math.floor(elapsedMin / 1440)}d ago`;

  return { level, ago };
}

/** Sort rank so stale/offline machines surface first. */
export function statusRank(level: MachineLevel): number {
  return { offline: 0, stale: 1, recent: 2, online: 3 }[level];
}

/** True when a machine has stopped reporting (needs attention). */
export function isDown(level: MachineLevel): boolean {
  return level === 'stale' || level === 'offline';
}
