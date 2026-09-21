<template>
  <div class="rounded-md bg-surface-white shadow">
    <!-- Header: title + pipeline scope toggle -->
    <div class="flex items-start justify-between gap-4 flex-wrap px-4 pt-4 pb-3">
      <div>
        <div class="text-lg font-semibold text-ink-gray-9">
          {{ __('Activity') }}
        </div>
        <div class="text-sm text-ink-gray-5 mt-0.5">
          {{ __('Everyone contacted in this range — calls, talk time, texts') }}
        </div>
      </div>
      <Tooltip
        :text="
          __(
            'Acq = New → Contract Sent · Dispo = Photos & Lockbox → Buyer Assigned · All = everyone, including dead and parked',
          )
        "
      >
        <div
          class="inline-flex rounded-md bg-surface-gray-2 p-0.5 text-sm font-medium"
        >
          <button
            v-for="s in SCOPES"
            :key="s.key"
            class="px-3 py-1 rounded transition-colors"
            :class="
              scope === s.key
                ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                : 'text-ink-gray-6 hover:text-ink-gray-8'
            "
            @click="setScope(s.key)"
          >
            {{ s.label }}
          </button>
        </div>
      </Tooltip>
    </div>

    <!-- Scope totals -->
    <div
      v-if="scoped.length"
      class="flex items-end gap-8 flex-wrap px-4 pb-3"
    >
      <div>
        <div class="text-xs uppercase tracking-wide text-ink-gray-5">
          {{ __('Contacted') }}
        </div>
        <div class="text-lg font-semibold text-ink-gray-9 tabular-nums">
          {{ fmt(scoped.length) }}
        </div>
      </div>
      <div>
        <div class="text-xs uppercase tracking-wide text-ink-gray-5">
          {{ __('Calls') }}
        </div>
        <div class="text-lg font-semibold tabular-nums">
          <span class="inline-flex items-center gap-0.5 text-ink-gray-9">
            <LucideArrowUpRight class="size-4" />{{ fmt(totals.calls_out) }}
          </span>
          <span
            class="inline-flex items-center gap-0.5 ml-3"
            :class="totals.calls_in ? 'text-ink-green-3' : 'text-ink-gray-4'"
          >
            <LucideArrowDownLeft class="size-4" />{{ fmt(totals.calls_in) }}
          </span>
        </div>
      </div>
      <div>
        <div class="text-xs uppercase tracking-wide text-ink-gray-5">
          {{ __('Talk time') }}
        </div>
        <div class="text-lg font-semibold text-ink-gray-9 tabular-nums">
          {{ formatDuration(totals.secs) || '0s' }}
        </div>
      </div>
      <div>
        <div class="text-xs uppercase tracking-wide text-ink-gray-5">
          {{ __('Texts') }}
        </div>
        <div class="text-lg font-semibold tabular-nums">
          <span class="inline-flex items-center gap-0.5 text-ink-gray-9">
            <LucideArrowUpRight class="size-4" />{{ fmt(totals.texts_out) }}
          </span>
          <span
            class="inline-flex items-center gap-0.5 ml-3"
            :class="totals.texts_in ? 'text-ink-green-3' : 'text-ink-gray-4'"
          >
            <LucideArrowDownLeft class="size-4" />{{ fmt(totals.texts_in) }}
          </span>
        </div>
      </div>
      <div v-if="totals.agreements">
        <div class="text-xs uppercase tracking-wide text-ink-gray-5">
          {{ __('Agreements') }}
        </div>
        <div class="text-lg font-semibold text-ink-gray-9 tabular-nums">
          {{ fmt(totals.agreements) }}
        </div>
      </div>
    </div>

    <!-- States -->
    <div
      v-if="contacted.loading"
      class="px-4 py-10 text-center text-ink-gray-5 border-t border-outline-gray-1"
    >
      {{ __('Loading…') }}
    </div>
    <div
      v-else-if="!scoped.length"
      class="px-4 py-10 text-center text-ink-gray-5 border-t border-outline-gray-1"
    >
      {{ emptyText }}
    </div>

    <!-- The ledger: one row per contacted lead -->
    <template v-else>
      <div class="border-t border-outline-gray-1 max-h-96 overflow-auto">
        <table class="w-full text-sm">
          <thead class="sticky top-0 bg-surface-white z-[1]">
            <tr class="text-ink-gray-5 text-xs uppercase tracking-wide">
              <th class="text-left font-medium py-2 pl-4 pr-2">
                {{ __('Lead') }}
              </th>
              <th class="text-left font-medium py-2 px-2 w-44 hidden sm:table-cell">
                {{ __('Status') }}
              </th>
              <th class="text-right font-medium py-2 px-2 w-28">
                {{ __('Calls / Texts') }}
              </th>
              <th
                class="text-right font-medium py-2 px-2 w-24"
                :class="hasAgreements ? '' : 'pr-4'"
              >
                {{ __('Talk time') }}
              </th>
              <th
                v-if="hasAgreements"
                class="text-right font-medium py-2 pl-2 pr-4 w-16"
              >
                {{ __('Agr') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="lead in scoped"
              :key="lead.name"
              class="border-t border-outline-gray-1 hover:bg-surface-gray-1 cursor-pointer"
              @click="openLead(lead.name)"
            >
              <td class="py-2 pl-4 pr-2">
                <span class="text-ink-gray-8 truncate block max-w-[7rem] sm:max-w-[16rem]">
                  {{ lead.lead_name }}
                </span>
              </td>
              <td class="py-2 px-2 hidden sm:table-cell">
                <div class="flex items-center gap-1.5 min-w-0">
                  <IndicatorIcon
                    :class="statusColor(lead.status)"
                    class="shrink-0"
                  />
                  <span class="text-xs text-ink-gray-6 truncate">
                    {{ lead.status }}
                  </span>
                </div>
              </td>
              <td class="text-right py-2 px-2 whitespace-nowrap">
                <template v-if="hasContact(lead)">
                  <div
                    v-if="lead.calls_out || lead.calls_in"
                    class="flex items-center justify-end gap-1"
                  >
                    <LucidePhone class="size-3 text-ink-gray-4 shrink-0" />
                    <span
                      class="inline-flex items-center gap-0.5 tabular-nums text-ink-gray-7"
                    >
                      <LucideArrowUpRight class="size-3" />{{ lead.calls_out }}
                    </span>
                    <span
                      class="inline-flex items-center gap-0.5 tabular-nums"
                      :class="
                        lead.calls_in
                          ? 'text-ink-green-3 font-medium'
                          : 'text-ink-gray-4'
                      "
                    >
                      <LucideArrowDownLeft class="size-3" />{{ lead.calls_in }}
                    </span>
                  </div>
                  <div
                    v-if="lead.texts_out || lead.texts_in"
                    class="flex items-center justify-end gap-1"
                    :class="lead.calls_out || lead.calls_in ? 'mt-0.5' : ''"
                  >
                    <LucideMessageSquare
                      class="size-3 text-ink-gray-4 shrink-0"
                    />
                    <span
                      class="inline-flex items-center gap-0.5 tabular-nums text-ink-gray-7"
                    >
                      <LucideArrowUpRight class="size-3" />{{ lead.texts_out }}
                    </span>
                    <span
                      class="inline-flex items-center gap-0.5 tabular-nums"
                      :class="
                        lead.texts_in
                          ? 'text-ink-green-3 font-medium'
                          : 'text-ink-gray-4'
                      "
                    >
                      <LucideArrowDownLeft class="size-3" />{{ lead.texts_in }}
                    </span>
                  </div>
                </template>
                <span v-else class="text-ink-gray-4">–</span>
              </td>
              <td
                class="text-right py-2 px-2 tabular-nums text-ink-gray-7"
                :class="hasAgreements ? '' : 'pr-4'"
              >
                {{ lead.secs ? formatDuration(lead.secs) : '–' }}
              </td>
              <td
                v-if="hasAgreements"
                class="text-right py-2 pl-2 pr-4 tabular-nums text-ink-gray-7"
              >
                {{ lead.agreements || '–' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Footer: truncation note + drill into the Leads list -->
      <div
        class="flex items-center justify-between gap-4 px-4 py-2 border-t border-outline-gray-1"
      >
        <div class="text-xs text-ink-gray-5">
          <template v-if="contacted.data?.truncated">
            {{ __('Showing the first {0} leads.', [fmt(leads.length)]) }}
          </template>
        </div>
        <Button
          variant="ghost"
          :label="__('Open all in Leads')"
          @click="openAll"
        >
          <template #suffix>
            <LucideArrowRight class="size-3.5" />
          </template>
        </Button>
      </div>
    </template>
  </div>
</template>

<script setup>
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import LucideArrowRight from '~icons/lucide/arrow-right'
import LucideArrowUpRight from '~icons/lucide/arrow-up-right'
import LucideArrowDownLeft from '~icons/lucide/arrow-down-left'
import LucidePhone from '~icons/lucide/phone'
import LucideMessageSquare from '~icons/lucide/message-square'
import { leadDrilldownStore } from '@/stores/leadDrilldown'
import { statusesStore } from '@/stores/statuses'
import { formatDuration } from '@/utils'
import { createResource, Button, Tooltip } from 'frappe-ui'
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

const SCOPES = [
  { key: 'acq', label: __('Acq') },
  { key: 'dispo', label: __('Dispo') },
  { key: 'all', label: __('All') },
]

const scope = ref(localStorage.getItem('activityScope') || 'acq')

function setScope(s) {
  if (scope.value === s) return
  scope.value = s
  localStorage.setItem('activityScope', s)
}

const contacted = createResource({
  url: 'crm.api.leads_dashboard.get_contacted_leads',
  makeParams() {
    return {
      from_date: props.fromDate,
      to_date: props.toDate,
      user: props.user,
    }
  },
  auto: true,
})

watch(
  () => [props.fromDate, props.toDate, props.user],
  () => contacted.reload(),
)

const leads = computed(() => contacted.data?.leads || [])

// The toggle filters client-side over the single fetch, so switching is instant.
const scoped = computed(() =>
  scope.value === 'all'
    ? leads.value
    : leads.value.filter((l) => l.bucket === scope.value),
)

const totals = computed(() =>
  scoped.value.reduce(
    (acc, l) => {
      acc.calls_out += l.calls_out
      acc.calls_in += l.calls_in
      acc.secs += l.secs
      acc.texts_out += l.texts_out
      acc.texts_in += l.texts_in
      acc.agreements += l.agreements
      return acc
    },
    { calls_out: 0, calls_in: 0, secs: 0, texts_out: 0, texts_in: 0, agreements: 0 },
  ),
)

const hasAgreements = computed(() => scoped.value.some((l) => l.agreements))

const emptyText = computed(() => {
  if (scope.value === 'acq')
    return __('No acq-pipeline leads contacted in this range')
  if (scope.value === 'dispo')
    return __('No dispo leads contacted in this range')
  return __('No contact activity in this range')
})

function hasContact(lead) {
  return lead.calls_out || lead.calls_in || lead.texts_out || lead.texts_in
}

function statusColor(status) {
  if (!status) return 'text-ink-gray-4'
  return getLeadStatus(status)?.color || 'text-ink-gray-4'
}

function fmt(n) {
  return (n ?? 0).toLocaleString()
}

function openLead(name) {
  router.push({ name: 'Lead', params: { leadId: name } })
}

function openAll() {
  const label = SCOPES.find((s) => s.key === scope.value)?.label || __('All')
  drilldown.set({
    names: scoped.value.map((l) => l.name),
    label: `${__('Contacted')} (${label})`,
    sub: `${props.fromDate} – ${props.toDate}`,
    truncated: contacted.data?.truncated,
  })
  router.push({ name: 'Leads', params: { viewType: 'list' } })
}
</script>
