// Registry-aware model identity for display (FR-MODEL-010, NFR-MAIN-006).
//
// The Claude-only assumption in the dashboard was that every model id could be
// humanized by the modelLabel parser and was therefore "known". Provider-neutral
// views instead consult the model registry (GET /api/v2/models): a native id
// present there (or under one of its aliases) is known and renders with a
// friendly display name; an absent id still renders (never dropped) but is
// flagged so the UI can show an "unknown" badge and surface it for registration.
//
// The display string still comes from the existing modelLabel humanizer, which
// gives Claude ids their friendly form and passes any other id through
// unchanged -- so Claude deployments read exactly as before while non-Claude
// ids show their native id. Pure and unit-tested.

import { modelLabel } from './format';
import type { ModelV2 } from '@/api/types-v2';

/** A model id resolved for display against the registry. */
export interface ResolvedModel {
  /** The provider's native model id, always shown verbatim somewhere. */
  native: string;
  /** Humanized display name (Claude ids friendly, others passed through). */
  display: string;
  /** Whether the registry recognizes this id (else the UI flags it unknown). */
  known: boolean;
}

/** The set of native model ids plus alias spellings the registry recognizes. */
export function knownModelIds(models: ModelV2[]): Set<string> {
  const ids = new Set<string>();
  for (const model of models) {
    ids.add(model.native_model_id);
    for (const alias of model.aliases) ids.add(alias);
  }
  return ids;
}

/**
 * Resolve a native model id to its display label and registry known-ness.
 *
 * ``display`` reuses the humanizer so Claude ids render as today and other ids
 * pass through; ``known`` is false when the id is absent from ``known`` so a
 * view can badge it and prompt registration rather than silently trusting it.
 */
export function resolveModel(
  nativeId: string,
  known: Set<string>
): ResolvedModel {
  return {
    native: nativeId,
    display: modelLabel(nativeId),
    known: known.has(nativeId),
  };
}
