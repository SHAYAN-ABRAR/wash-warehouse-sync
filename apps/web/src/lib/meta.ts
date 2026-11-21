import {
  AlertTriangle,
  Ban,
  CalendarPlus,
  CheckCircle2,
  Copy,
  Eye,
  FileText,
  Fingerprint,
  GitMerge,
  MapPinPlus,
  PaintBucket,
  PencilLine,
  type LucideIcon,
} from 'lucide-react'
import type { ChangeClass, Decision, Source } from './types'

export type Tone = 'success' | 'info' | 'warning' | 'danger' | 'neutral' | 'violet' | 'teal'

export interface ClassMeta {
  label: string
  tone: Tone
  icon: LucideIcon
  description: string
  auto: boolean
}

export const CLASS_META: Record<ChangeClass, ClassMeta> = {
  NEW_FACILITY: {
    label: 'New facility',
    tone: 'success',
    icon: MapPinPlus,
    auto: true,
    description:
      'No warehouse row carries this source identity and no compatible facility is nearby. A new row is inserted inside its administrative group.',
  },
  NEW_OBSERVATION: {
    label: 'New observation',
    tone: 'info',
    icon: CalendarPlus,
    auto: true,
    description:
      'A new survey year/season, new geological layers or a verified cross-source enrichment for an existing facility row.',
  },
  FILLED_MISSING_VALUE: {
    label: 'Filled blank value',
    tone: 'teal',
    icon: PaintBucket,
    auto: true,
    description: 'A previously blank cell is filled from a verified source identity. Existing values are never overwritten.',
  },
  NEW_SOURCE_METADATA: {
    label: 'Metadata update',
    tone: 'violet',
    icon: FileText,
    auto: false,
    description: 'Descriptive source fields (status, dates, GPS text) differ from the warehouse. Requires a decision.',
  },
  UPDATED_MEASUREMENT: {
    label: 'Changed measurement',
    tone: 'warning',
    icon: PencilLine,
    auto: false,
    description: 'An existing measurement differs from the incoming source. Never overwritten silently — requires review.',
  },
  UNCHANGED: {
    label: 'Unchanged',
    tone: 'neutral',
    icon: CheckCircle2,
    auto: true,
    description: 'Identical content is already in the warehouse. Skipped without any change.',
  },
  EXACT_DUPLICATE: {
    label: 'Exact duplicate',
    tone: 'neutral',
    icon: Copy,
    auto: true,
    description: 'The same observation appears earlier in this run with identical content. Skipped.',
  },
  POTENTIAL_DUPLICATE: {
    label: 'Possible duplicate',
    tone: 'warning',
    icon: GitMerge,
    auto: false,
    description:
      'Nearby compatible facility found, but GPS or location alone is never proof of identity. Review decides whether to merge or keep separate.',
  },
  CONFLICTING_IDENTITY: {
    label: 'Identity conflict',
    tone: 'danger',
    icon: Fingerprint,
    auto: false,
    description: 'Identifiers disagree (reused geocode, duplicate ID, failed cross-source verification). Merging is blocked until reviewed.',
  },
  CONFLICTING_MEASUREMENT: {
    label: 'Measurement conflict',
    tone: 'danger',
    icon: AlertTriangle,
    auto: false,
    description: 'The same observation appears more than once in this run with different values.',
  },
  INVALID_SOURCE_RECORD: {
    label: 'Invalid record',
    tone: 'danger',
    icon: Ban,
    auto: false,
    description: 'Failed validation (for example a missing source ID or an unrecognized survey period). Not imported.',
  },
  REQUIRES_REVIEW: {
    label: 'Needs review',
    tone: 'warning',
    icon: Eye,
    auto: false,
    description: 'Otherwise valid, but a rule requires a human decision (suspected test record, historical period, overlong profile).',
  },
}

export const CLASS_ORDER: ChangeClass[] = [
  'NEW_FACILITY',
  'NEW_OBSERVATION',
  'FILLED_MISSING_VALUE',
  'UPDATED_MEASUREMENT',
  'NEW_SOURCE_METADATA',
  'POTENTIAL_DUPLICATE',
  'CONFLICTING_IDENTITY',
  'CONFLICTING_MEASUREMENT',
  'REQUIRES_REVIEW',
  'INVALID_SOURCE_RECORD',
  'EXACT_DUPLICATE',
  'UNCHANGED',
]

export const TONE_CLASSES: Record<Tone, { badge: string; dot: string; soft: string; chart: string }> = {
  success: { badge: 'bg-success/10 text-success ring-success/25', dot: 'bg-success', soft: 'bg-success/10', chart: 'hsl(160 70% 34%)' },
  info: { badge: 'bg-info/10 text-info ring-info/25', dot: 'bg-info', soft: 'bg-info/10', chart: 'hsl(210 85% 48%)' },
  teal: { badge: 'bg-primary/10 text-primary ring-primary/25', dot: 'bg-primary', soft: 'bg-primary/10', chart: 'hsl(186 80% 36%)' },
  warning: { badge: 'bg-warning/10 text-warning ring-warning/25', dot: 'bg-warning', soft: 'bg-warning/10', chart: 'hsl(36 92% 48%)' },
  danger: { badge: 'bg-destructive/10 text-destructive ring-destructive/25', dot: 'bg-destructive', soft: 'bg-destructive/10', chart: 'hsl(0 72% 52%)' },
  violet: { badge: 'bg-violet-500/10 text-violet-600 ring-violet-500/25 dark:text-violet-300', dot: 'bg-violet-500', soft: 'bg-violet-500/10', chart: 'hsl(262 60% 58%)' },
  neutral: { badge: 'bg-muted text-muted-foreground ring-border', dot: 'bg-muted-foreground/60', soft: 'bg-muted', chart: 'hsl(215 16% 62%)' },
}

export const SOURCE_META: Record<Source, { label: string; long: string; color: string }> = {
  GPANI: { label: 'GPANI', long: 'Grameen Pani tubewell registry', color: 'hsl(186 80% 36%)' },
  WPMS: { label: 'WPMS', long: 'Water Point Management System', color: 'hsl(222 47% 45%)' },
  AQUAWATCH: { label: 'AquaWatch', long: 'Groundwater monitoring surveys', color: 'hsl(262 55% 58%)' },
  BORELOG: { label: 'Bore Log', long: 'Bore-log engineering records', color: 'hsl(160 55% 38%)' },
}

export const DECISION_META: Record<Decision, { label: string; tone: Tone }> = {
  APPROVE_MATCH: { label: 'Approve match', tone: 'info' },
  KEEP_SEPARATE: { label: 'Keep separate', tone: 'success' },
  ACCEPT_NEW_VALUES: { label: 'Accept new values', tone: 'warning' },
  KEEP_EXISTING: { label: 'Keep existing', tone: 'neutral' },
  ACCEPT: { label: 'Accept', tone: 'success' },
  REJECT: { label: 'Reject', tone: 'danger' },
  DEFER: { label: 'Defer', tone: 'neutral' },
}

export const MATCH_LEVEL_META: Record<string, { label: string; description: string }> = {
  L1_SOURCE_ID: { label: 'Level 1 · exact source ID', description: 'A trusted, source-issued identifier matched exactly.' },
  L2_CROSSWALK: { label: 'Level 2 · reviewed crosswalk', description: 'An explicitly approved cross-source link from an earlier review.' },
  L2_VERIFIED_CROSS_SOURCE: {
    label: 'Level 2 · verified shared code',
    description: 'A shared government code verified by administrative location, GPS, tubewell type and depth.',
  },
  L3_GEOGRAPHIC: { label: 'Level 3 · geographic candidate', description: 'Location only — never merged without a reviewer decision.' },
  WITHIN_RUN: { label: 'Same run', description: 'The facility is created by another record in the same synchronization run.' },
  NONE: { label: 'No match', description: 'No warehouse row matched this source identity.' },
}

export const GLOSSARY: Record<string, string> = {
  'Dry run': 'A preview that classifies every record and writes a plan and audit, without changing the warehouse.',
  Plan: 'The exact list of proposed cell changes for one base version, plus review items. Applied only after approval.',
  Fingerprint: 'A SHA-256 hash of a record’s identity and compared content. Formatting-only differences (5.50 vs 5.5) do not change it.',
  Crosswalk: 'Reviewed links between source identities and warehouse facilities (including “keep separate” decisions).',
  Provenance: 'Where a value came from: source file, row and the plan that wrote it. Never treated as a data change by itself.',
}
