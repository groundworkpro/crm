<template>
  <div class="rounded-md bg-surface-white shadow">
    <!-- Header: title + mode toggle -->
    <div
      class="flex items-start justify-between gap-4 flex-wrap px-4 pt-4 pb-3"
    >
      <div>
        <div class="text-lg font-semibold text-ink-gray-9">
          {{ __('Status Change Report') }}
        </div>
        <div class="text-sm text-ink-gray-5 mt-0.5">
          {{
            mode === 'cohort'
              ? __('Leads created in this range, and where each one went')
              : __('How leads moved between statuses in this range')
          }}
        </div>
      </div>

      <div class="flex items-center gap-2">
        <Tooltip :text="__('Acquisition = New through Contract Sent')">
          <div
            class="inline-flex rounded-md bg-surface-gray-2 p-0.5 text-sm font-medium"
          >
            <button
              v-for="s in ['acquisition', 'all']"
              :key="s"
              class="px-3 py-1 rounded transition-colors"
              :class="
                scope === s
                  ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                  : 'text-ink-gray-6 hover:text-ink-gray-8'
              "
              @click="setScope(s)"
            >
              {{ s === 'acquisition' ? __('Acquisition') : __('All stages') }}
            </button>
          </div>
        </Tooltip>
        <Tooltip
          :text="
            mode === 'cohort'
              ? __('Date range = when the lead was created')
              : __('Date range = when the status change happened')
          "
        >
          <div
            class="inline-flex rounded-md bg-surface-gray-2 p-0.5 text-sm font-medium"
          >
            <button
              v-for="m in ['cohort', 'flow']"
              :key="m"
              class="px-3 py-1 rounded transition-colors"
              :class="
                mode === m
                  ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                  : 'text-ink-gray-6 hover:text-ink-gray-8'
              "
              @click="setMode(m)"
            >
              {{ m === 'cohort' ? __('Cohort') : __('Flow') }}
            </button>
          </div>
        </Tooltip>
      </div>
    </div>

    <!-- States -->
    <div v-if="report.loading" class="px-4 py-10 text-center text-ink-gray-5">
      {{ __('Loading…') }}
    </div>
    <div
      v-else-if="!visibleStages.length"
      class="px-4 py-10 text-center text-ink-gray-5"
    >
      {{
        mode === 'cohort'
          ? __('No leads created in this range')
          : __('No status changes in this range')
      }}
    </div>

    <!-- Table (scrolls horizontally inside the card on narrow screens) -->
    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[520px] text-sm border-t border-outline-gray-1">
      <thead>
        <tr class="text-ink-gray-5 text-xs uppercase tracking-wide">
          <th class="text-left font-medium py-2 pl-4 pr-2">
            {{ __('Status') }}
          </th>
          <th class="text-right font-medium py-2 px-2 w-20">
            <Tooltip
              :text="
                mode === 'cohort'
                  ? __('Times a lead moved into this status')
                  : __('Leads that arrived in this status (created or moved in)')
              "
            >
              <span>{{ __('Entered') }}</span>
            </Tooltip>
          </th>
          <th class="text-right font-medium py-2 px-2 w-20">
            <Tooltip :text="__('Leads that moved out of this status')">
              <span>{{ __('Left') }}</span>
            </Tooltip>
          </th>
          <th class="text-right font-medium py-2 px-2 w-44">
            <Tooltip
              :text="
                mode === 'cohort'
                  ? __('Where the leads started → where they are now')
                  : __('Count at the start → at the end of the range')
              "
            >
              <span>{{ __('Started') }} → {{ __('Ended') }}</span>
            </Tooltip>
          </th>
          <th class="text-right font-medium py-2 pl-2 pr-4 w-28">
            {{ __('Net Change') }}
          </th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in visibleStages" :key="row.stage">
          <tr
            class="border-t border-outline-gray-1 hover:bg-surface-gray-1 cursor-pointer"
            @click="toggle(row.stage)"
          >
            <td class="py-2 pl-4 pr-2">
              <div class="flex items-center gap-2">
                <LucideChevronRight
                  class="size-3.5 text-ink-gray-5 transition-transform shrink-0"
                  :class="expanded.has(row.stage) ? 'rotate-90' : ''"
                />
                <IndicatorIcon :class="statusColor(row.stage)" class="shrink-0" />
                <span class="text-ink-gray-8 truncate">{{ row.stage }}</span>
              </div>
            </td>
            <td class="text-right py-2 px-2 tabular-nums text-ink-gray-7">
              {{ row.entered || '–' }}
            </td>
            <td class="text-right py-2 px-2 tabular-nums text-ink-gray-7">
              {{ row.left || '–' }}
            </td>
            <td class="text-right py-2 px-2 tabular-nums text-ink-gray-7">
              <span>{{ fmt(row.started) }}</span>
              <span class="text-ink-gray-4 mx-1">→</span>
              <span>{{ fmt(row.ended) }}</span>
            </td>
            <td class="text-right py-2 pl-2 pr-4 tabular-nums">
              <span :class="netClass(row.net)">{{ netLabel(row.net) }}</span>
              <span
                v-if="row.net_pct !== null && row.net !== 0"
                class="text-ink-gray-4 text-xs ml-1"
                >({{ row.net_pct > 0 ? '+' : '' }}{{ row.net_pct }}%)</span
              >
            </td>
          </tr>

          <!-- Unfolded flow diagram -->
          <tr v-if="expanded.has(row.stage)" :key="row.stage + '-flow'">
            <td colspan="5" class="bg-surface-gray-1 px-4 py-4">
              <div
                v-if="!row.inflows.length && !row.outflows.length"
                class="text-ink-gray-5 text-sm"
              >
                {{ __('No recorded transitions for this status in this range.') }}
              </div>
              <div v-else class="flex items-stretch gap-3 flex-wrap">
                <!-- Came from -->
                <div class="flex-1 min-w-[200px]">
                  <div
                    class="text-xs uppercase tracking-wide text-ink-gray-5 mb-2"
                  >
                    {{ __('Came from') }}
                  </div>
                  <div v-if="row.inflows.length" class="flex flex-col gap-1.5">
                    <FlowEdge
                      v-for="(f, i) in row.inflows"
                      :key="'in' + i"
                      :stage="f.source"
                      :count="f.count"
                      direction="in"
                      :color="statusColor(f.source)"
                      :label="sourceLabel(f.source)"
                      @click="
                        drill({
                          fromStage: f.source,
                          toStage: row.stage,
                          label: `${sourceLabel(f.source)} → ${row.stage}`,
                        })
                      "
                    />
                  </div>
                  <div v-else class="text-ink-gray-4 text-sm">—</div>
                </div>

                <!-- Center stage node -->
                <div class="flex items-center">
                  <div
                    class="flex items-center gap-2 rounded-md border border-outline-gray-2 bg-surface-white px-3 py-2 shadow-sm"
                  >
                    <IndicatorIcon :class="statusColor(row.stage)" />
                    <span class="font-medium text-ink-gray-9">{{
                      row.stage
                    }}</span>
                  </div>
                </div>

                <!-- Went to -->
                <div class="flex-1 min-w-[200px]">
                  <div
                    class="text-xs uppercase tracking-wide text-ink-gray-5 mb-2"
                  >
                    {{ __('Went to') }}
                  </div>
                  <div v-if="row.outflows.length" class="flex flex-col gap-1.5">
                    <FlowEdge
                      v-for="(o, i) in row.outflows"
                      :key="'out' + i"
                      :stage="o.dest"
                      :count="o.count"
                      direction="out"
                      :color="statusColor(o.dest)"
                      :label="o.dest"
                      @click="
                        drill({
                          fromStage: row.stage,
                          toStage: o.dest,
                          label: `${row.stage} → ${o.dest}`,
                        })
                      "
                    />
                  </div>
                  <div v-else class="text-ink-gray-4 text-sm">—</div>
                </div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import FlowEdge from '@/components/Dashboard/FlowEdge.vue'
import LucideChevronRight from '~icons/lucide/chevron-right'
import { statusesStore } from '@/stores/statuses'
import { leadDrilldownStore } from '@/stores/leadDrilldown'
import { createResource, Tooltip } from 'frappe-ui'
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  fromDate: { type: String, default: null },
  toDate: { type: String, default: null },
  user: { type: String, default: null },
})

const router = useRouter()
const drilldown = leadDrilldownStore()
const { getLeadStatus } = statusesStore()

const CREATED = '__created__'
const mode = ref('cohort')
const expanded = ref(new Set())

const report = createResource({
  url: 'crm.api.leads_dashboard.get_status_change_report',
  makeParams() {
    return {
      from_date: props.fromDate,
      to_date: props.toDate,
      user: props.user,
      mode: mode.value,
    }
  },
  auto: true,
})

const stages = computed(() => report.data?.stages || [])

// Acquisition-phase statuses (New → Contract Sent). Dispo and parking/terminal
// stages are hidden as rows in Acquisition scope but still show inside a row's
// unfolded Came-from / Went-to flows (so leakage to Dead Lead etc. stays visible).
const ACQ_STAGES = new Set([
  'New',
  'Called No Answer',
  'Follow Up',
  'Underwriting',
  'Make Offer',
  'Contract Sent',
])

const scope = ref(localStorage.getItem('statusReportScope') || 'acquisition')

const visibleStages = computed(() =>
  scope.value === 'acquisition'
    ? stages.value.filter((s) => ACQ_STAGES.has(s.stage))
    : stages.value,
)

function setScope(s) {
  if (scope.value === s) return
  scope.value = s
  localStorage.setItem('statusReportScope', s)
}

// Reload when the dashboard's date range / user changes.
watch(
  () => [props.fromDate, props.toDate, props.user],
  () => report.reload(),
)

function setMode(m) {
  if (mode.value === m) return
  mode.value = m
  expanded.value = new Set()
  report.reload()
}

function toggle(stage) {
  const next = new Set(expanded.value)
  next.has(stage) ? next.delete(stage) : next.add(stage)
  expanded.value = next
}

function statusColor(stage) {
  if (!stage || stage === CREATED) return 'text-ink-gray-4'
  return getLeadStatus(stage)?.color || 'text-ink-gray-4'
}

function sourceLabel(source) {
  return source === CREATED ? __('Created') : source
}

function fmt(n) {
  return (n ?? 0).toLocaleString()
}

function netLabel(net) {
  if (!net) return '–'
  return net > 0 ? `+${fmt(net)}` : fmt(net)
}

function netClass(net) {
  if (!net) return 'text-ink-gray-4'
  return net > 0 ? 'text-ink-green-3' : 'text-ink-red-3'
}

const resolver = createResource({
  url: 'crm.api.leads_dashboard.get_status_transition_leads',
})

async function drill({ fromStage, toStage, label }) {
  const res = await resolver.submit({
    to_stage: toStage,
    from_stage: fromStage,
    from_date: props.fromDate,
    to_date: props.toDate,
    user: props.user,
    mode: mode.value,
  })
  if (!res) return
  drilldown.set({
    names: res.names,
    label,
    sub: drillSub(),
    truncated: res.truncated,
  })
  router.push({ name: 'Leads', params: { viewType: 'list' } })
}

function drillSub() {
  const what = mode.value === 'cohort' ? __('created') : __('changed')
  return `${what} ${props.fromDate} – ${props.toDate}`
}
</script>
