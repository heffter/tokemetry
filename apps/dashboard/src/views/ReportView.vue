<script setup lang="ts">
// Token-optimization report: an executive summary, a health scorecard with
// red/yellow/green badges, ranked recommendations, and per-project and
// per-machine breakdowns with config-drift callouts.
import { computed, onMounted, ref } from 'vue';
import StatTile from '@/components/StatTile.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { formatPct, formatTokens } from '@/lib/format';
import { presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type { Report, ReportDimension } from '@/api/types';

const { loading, error, run, retry } = useAsync();
const report = ref<Report | null>(null);
const filter = ref<UsageFilter>(presetRange('30d'));

async function load(): Promise<void> {
  await run(async () => {
    report.value = await useClient().report(filter.value.from, filter.value.to);
  });
}

function onFilter(next: UsageFilter): void {
  filter.value = next;
  void load();
}

const exporting = ref<'compact' | 'full' | null>(null);
const exportError = ref('');

// Download the LLM-ready Markdown export, triggering a browser file save.
async function downloadExport(size: 'compact' | 'full'): Promise<void> {
  exporting.value = size;
  exportError.value = '';
  try {
    const markdown = await useClient().reportExport(
      size,
      filter.value.from,
      filter.value.to
    );
    const blob = new Blob([markdown], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `tokemetry-report-${size}.md`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    exportError.value = err instanceof Error ? err.message : 'export failed';
  } finally {
    exporting.value = null;
  }
}

// Higher-is-better metric -> a status class by two thresholds.
function goodHigh(value: number, warn: number, good: number): string {
  if (value >= good) return 'badge-good';
  if (value >= warn) return 'badge-warning';
  return 'badge-critical';
}
// Lower-is-better metric.
function goodLow(value: number, warn: number, crit: number): string {
  if (value <= warn) return 'badge-good';
  if (value <= crit) return 'badge-warning';
  return 'badge-critical';
}

const reclaimable = computed(() => {
  const recs = report.value?.recommendations ?? [];
  return recs.reduce((sum, r) => sum + (r.impact_tokens ?? 0), 0);
});

function severityBadge(severity: string): string {
  if (severity === 'critical') return 'badge-critical';
  if (severity === 'warning') return 'badge-warning';
  return 'badge-muted';
}

function dimLabel(d: ReportDimension): string {
  return d.name || '(unattributed)';
}

onMounted(load);
</script>

<template>
  <div>
    <FilterBar @change="onFilter" />
    <AsyncState :loading="loading && !report" :error="error" @retry="retry">
      <template v-if="report">
        <section class="grid tiles">
          <StatTile
            label="Tokens in range"
            :value="formatTokens(report.scorecard.total_tokens)"
            :sub="`${report.scorecard.session_count} sessions · ${report.scorecard.machine_count} machine(s)`"
          />
          <StatTile
            label="Cache-hit-rate"
            :value="formatPct(report.scorecard.cache_hit_rate * 100)"
            sub="target ≥ 85%"
          />
          <StatTile
            label="Reclaimable (est.)"
            :value="formatTokens(reclaimable)"
            sub="from the recommendations below"
          />
          <StatTile
            label="Verbosity"
            :value="formatPct(report.scorecard.verbosity_ratio * 100)"
            sub="output / input · target ≤ 30%"
          />
        </section>

        <section class="card export">
          <div class="export-copy">
            <h3>Export for AI analysis</h3>
            <p class="muted small">
              Download a self-contained Markdown report with an embedded prompt
              to paste into your AI agent for optimization advice.
            </p>
          </div>
          <div class="export-actions">
            <button
              class="btn"
              :disabled="exporting !== null"
              @click="downloadExport('compact')"
            >
              {{ exporting === 'compact' ? 'Preparing…' : 'Compact' }}
            </button>
            <button
              class="btn"
              :disabled="exporting !== null"
              @click="downloadExport('full')"
            >
              {{ exporting === 'full' ? 'Preparing…' : 'Full' }}
            </button>
          </div>
          <p v-if="exportError" class="error small">{{ exportError }}</p>
        </section>

        <section class="card">
          <h3>Health scorecard</h3>
          <div class="scorecard">
            <div class="metric">
              <span
                class="badge"
                :class="goodHigh(report.scorecard.cache_hit_rate, 0.7, 0.85)"
                >{{ formatPct(report.scorecard.cache_hit_rate * 100) }}</span
              >
              <span class="muted">cache-hit-rate</span>
            </div>
            <div class="metric">
              <span
                class="badge"
                :class="goodLow(report.scorecard.verbosity_ratio, 0.3, 0.5)"
                >{{ formatPct(report.scorecard.verbosity_ratio * 100) }}</span
              >
              <span class="muted">verbosity</span>
            </div>
            <div class="metric">
              <span class="badge badge-muted">{{
                formatTokens(
                  Math.round(report.scorecard.median_tokens_per_turn)
                )
              }}</span>
              <span class="muted">median tokens/turn</span>
            </div>
            <div class="metric">
              <span
                class="badge"
                :class="goodHigh(report.scorecard.sidechain_share, 0.02, 0.05)"
                >{{ formatPct(report.scorecard.sidechain_share * 100) }}</span
              >
              <span class="muted">subagent share</span>
            </div>
            <div class="metric">
              <span
                class="badge"
                :class="goodLow(report.scorecard.unattributed_share, 0.15, 0.3)"
                >{{
                  formatPct(report.scorecard.unattributed_share * 100)
                }}</span
              >
              <span class="muted">unattributed</span>
            </div>
          </div>
        </section>

        <section class="card">
          <h3>Recommendations</h3>
          <ul class="recs">
            <li v-for="rec in report.recommendations" :key="rec.id" class="rec">
              <div class="rec-head">
                <span class="badge" :class="severityBadge(rec.severity)">{{
                  rec.severity
                }}</span>
                <strong>{{ rec.title }}</strong>
                <span class="muted small effort">effort {{ rec.effort }}</span>
                <span v-if="rec.impact_tokens" class="muted small">
                  ~{{ formatTokens(rec.impact_tokens) }}/period
                </span>
              </div>
              <p class="muted evidence">{{ rec.evidence }}</p>
              <p v-if="rec.affected.length" class="muted small">
                affects: {{ rec.affected.join(', ') }}
              </p>
            </li>
            <li v-if="report.recommendations.length === 0" class="muted">
              No optimization issues found in this range — usage looks healthy.
            </li>
          </ul>
        </section>

        <section class="card">
          <h3>By project</h3>
          <div class="scroll">
            <table>
              <thead>
                <tr>
                  <th>Project</th>
                  <th class="num">Tokens</th>
                  <th class="num">Cache hit</th>
                  <th class="num">Tokens/turn</th>
                  <th class="num">Verbosity</th>
                  <th class="num">Sessions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in report.projects" :key="d.name">
                  <td>{{ dimLabel(d) }}</td>
                  <td class="num tabular">
                    {{ formatTokens(d.total_tokens) }}
                  </td>
                  <td class="num tabular">
                    {{ formatPct(d.cache_hit_rate * 100) }}
                  </td>
                  <td class="num tabular">
                    {{ formatTokens(Math.round(d.median_tokens_per_turn)) }}
                  </td>
                  <td class="num tabular">
                    {{ formatPct(d.verbosity_ratio * 100) }}
                  </td>
                  <td class="num tabular">{{ d.session_count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <h3>By machine</h3>
          <div class="scroll">
            <table>
              <thead>
                <tr>
                  <th>Machine</th>
                  <th class="num">Tokens</th>
                  <th class="num">Cache hit</th>
                  <th class="num">Tokens/turn</th>
                  <th class="num">Verbosity</th>
                  <th class="num">Sessions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in report.machines" :key="d.name">
                  <td>{{ dimLabel(d) }}</td>
                  <td class="num tabular">
                    {{ formatTokens(d.total_tokens) }}
                  </td>
                  <td class="num tabular">
                    {{ formatPct(d.cache_hit_rate * 100) }}
                  </td>
                  <td class="num tabular">
                    {{ formatTokens(Math.round(d.median_tokens_per_turn)) }}
                  </td>
                  <td class="num tabular">
                    {{ formatPct(d.verbosity_ratio * 100) }}
                  </td>
                  <td class="num tabular">{{ d.session_count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </AsyncState>
  </div>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
h3 {
  margin: 0 0 1rem;
}
.scorecard {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  align-items: flex-start;
}
.recs {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rec {
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--border);
}
.rec:last-child {
  border-bottom: none;
}
.rec-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.effort {
  margin-left: auto;
}
.evidence {
  margin: 0.35rem 0 0;
  font-size: 0.9rem;
}
.small {
  font-size: 0.8rem;
}
.scroll {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.num {
  text-align: right;
}
.export {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
.export-copy {
  flex: 1 1 240px;
}
.export-copy h3 {
  margin-bottom: 0.35rem;
}
.export-copy p {
  margin: 0;
}
.export-actions {
  display: flex;
  gap: 0.5rem;
}
.btn {
  font: inherit;
  padding: 0.5rem 1rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  cursor: pointer;
  font-weight: 600;
}
.btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.error {
  color: var(--status-critical);
  flex-basis: 100%;
  margin: 0;
}
</style>
