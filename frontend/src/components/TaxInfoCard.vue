<template>
  <div :class="embedded ? 'rounded-lg border border-outline-gray-2' : 'border-t'">
    <div class="flex items-center justify-between px-5 py-3">
      <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
        <MoneyIcon class="size-4 text-ink-gray-7" />
        {{ __('Tax / liens') }}
      </div>
      <Button
        :tooltip="latest ? __('Re-pull tax & lien records') : __('Fetch tax & lien records')"
        icon="rotate-ccw"
        variant="ghost"
        @click="emit('fetch')"
      />
    </div>

    <div v-if="latest" class="px-5 pb-3 flex flex-col gap-1.5 text-sm">
      <div v-if="!latest.matched" class="text-ink-gray-5">
        {{ __('No property match found for this address.') }}
      </div>
      <template v-else>
        <Row v-if="latest.owner_name" :label="__('Owner')">
          {{ latest.owner_name }}
        </Row>
        <Row v-if="dd.mailing" :label="__('Mailing')">{{ dd.mailing }}</Row>
        <Row v-if="latest.owner_status_type" :label="__('Owner type')">
          {{ latest.owner_status_type
          }}<span v-if="latest.owner_occupied" class="text-ink-gray-5">
            · {{ __('owner-occupied') }}</span
          >
        </Row>
        <Row v-if="latest.apn" :label="__('APN')">{{ latest.apn }}</Row>
        <Row v-if="latest.tax_status" :label="__('Tax status')">
          <span
            :class="
              latest.tax_default || latest.tax_delinquent_year
                ? 'font-medium text-ink-red-3'
                : 'text-ink-gray-8'
            "
            >{{ latest.tax_status }}</span
          >
        </Row>
        <Row v-if="latest.annual_tax" :label="__('Annual tax')">
          ${{ formatNumber(latest.annual_tax)
          }}<span v-if="latest.tax_year" class="text-ink-gray-5">
            ({{ latest.tax_year }})</span
          >
        </Row>
        <Row v-if="latest.assessed_value" :label="__('Assessed value')">
          ${{ formatNumber(latest.assessed_value) }}
        </Row>
        <Row v-if="latest.estimated_value" :label="__('Est. value')">
          ${{ formatNumber(latest.estimated_value) }}
        </Row>
        <Row :label="__('Open liens')">
          {{ dd.open_lien_count ? dd.open_lien_count : __('None') }}
        </Row>
        <Row v-if="dd.free_and_clear" :label="__('Title')">
          {{ __('Free and clear') }}
        </Row>

        <button
          v-if="dd.report"
          type="button"
          class="mt-1.5 flex w-full items-center justify-between rounded-md border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-1.5 text-xs font-medium text-ink-gray-7 hover:bg-surface-gray-2"
          @click="showReport = true"
        >
          <span>{{ __('Full property report') }}</span>
          <FeatherIcon name="maximize-2" class="size-3.5 shrink-0" />
        </button>

        <button
          v-if="hasRecords"
          type="button"
          class="mt-1.5 flex w-full items-center justify-between rounded-md border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-1.5 text-xs font-medium text-ink-gray-7 hover:bg-surface-gray-2"
          @click="open = !open"
        >
          <span>{{ __('Records') }}</span>
          <FeatherIcon :name="open ? 'chevron-up' : 'chevron-down'" class="size-3.5" />
        </button>

        <div v-if="open && hasRecords" class="mt-1 flex flex-col gap-3 text-sm text-ink-gray-8">
          <section v-if="dd.foreclosure">
            <h4 class="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-gray-5">
              {{ __('Foreclosure') }}
            </h4>
            <div class="flex flex-col gap-1">
              <Row v-for="row in foreclosureRows" :key="row[0]" :label="row[0]">
                {{ row[1] }}
              </Row>
            </div>
          </section>

          <section v-if="dd.deeds?.length">
            <h4 class="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-gray-5">
              {{ __('Deeds') }}
            </h4>
            <div
              v-for="(d, i) in dd.deeds"
              :key="i"
              class="border-t border-outline-gray-2 py-1.5 first:border-t-0 first:pt-0"
            >
              <div class="flex justify-between gap-2 text-ink-gray-8">
                <span>{{ d.date || '—' }} · {{ d.type || __('Deed') }}</span>
                <span class="shrink-0">{{ d.price ? '$' + formatNumber(d.price) : '—' }}</span>
              </div>
              <div class="text-ink-gray-7">
                {{ (d.sellers || []).join(', ') || '—' }}
                → {{ (d.buyers || []).join(', ') || '—' }}
                <span v-if="d.foreclosure" class="text-ink-red-3"> · {{ __('FCL') }}</span>
              </div>
            </div>
          </section>

          <section v-if="dd.mortgages?.length">
            <h4 class="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-gray-5">
              {{ __('Mortgages') }}
            </h4>
            <div
              v-for="(m, i) in dd.mortgages"
              :key="i"
              class="border-t border-outline-gray-2 py-1.5 first:border-t-0 first:pt-0"
            >
              <div class="flex justify-between gap-2 text-ink-gray-8">
                <span>{{ m.date || '—' }} · {{ m.lender || '—' }}</span>
                <span class="shrink-0">{{ m.amount ? '$' + formatNumber(m.amount) : '—' }}</span>
              </div>
              <div v-if="m.type || m.rate" class="text-ink-gray-7">
                {{ m.type || '—' }}
                <span v-if="m.rate"> · {{ m.rate }}%</span>
              </div>
            </div>
          </section>

          <section v-if="dd.taxes?.length">
            <h4 class="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-gray-5">
              {{ __('Tax history') }}
            </h4>
            <Row v-for="(t, i) in dd.taxes" :key="i" :label="String(t.year || '—')">
              {{ t.amount ? '$' + formatNumber(t.amount) : '—' }}
            </Row>
          </section>
        </div>
      </template>

      <div class="mt-1 text-xs text-ink-gray-5">
        {{ __('Pulled by') }}
        <span class="text-ink-gray-7">{{
          latest.pulled_by_name || latest.pulled_by
        }}</span>
        · {{ formatDate(latest.pulled_at || latest.creation, '', true) }}
        <span v-if="pulls.length > 1" class="text-ink-gray-4">
          · {{ pulls.length }} {{ __('pulls') }}</span
        >
      </div>
    </div>

    <div v-else class="px-5 pb-3 text-sm text-ink-gray-5">
      {{ __('No tax info pulled yet.') }}
    </div>
    <TaxReportModal v-if="showReport" v-model="showReport" :report="dd.report" />
  </div>
</template>

<script setup>
import MoneyIcon from '@/components/Icons/MoneyIcon.vue'
import TaxReportModal from '@/components/Modals/TaxReportModal.vue'
import { formatDate, formatNumber } from '@/utils'
import { globalStore } from '@/stores/global'
import { Button, FeatherIcon, createResource } from 'frappe-ui'
import { computed, h, onMounted, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  lead: { type: String, required: true },
  embedded: { type: Boolean, default: false },
})

const emit = defineEmits(['fetch'])

const { $socket } = globalStore()
const open = ref(false)
const showReport = ref(false)

const Row = (p, { slots }) =>
  h('div', { class: 'flex gap-1.5' }, [
    h('span', { class: 'shrink-0 text-ink-gray-5' }, p.label),
    h('span', { class: 'text-ink-gray-8' }, slots.default?.()),
  ])

const taxPulls = createResource({
  url: 'crm.api.tax_info.get_tax_pulls',
  cache: ['tax_pulls', props.lead],
  params: { lead: props.lead },
  auto: true,
})

const pulls = computed(() => taxPulls.data || [])
const latest = computed(() => pulls.value[0] || null)
const dd = computed(() => latest.value?.dd || {})
const hasRecords = computed(
  () =>
    !!(
      dd.value.foreclosure ||
      dd.value.deeds?.length ||
      dd.value.mortgages?.length ||
      dd.value.taxes?.length
    ),
)

const foreclosureRows = computed(() => {
  const f = dd.value.foreclosure
  if (!f) return []
  return [
    [__('Status'), f.status],
    [__('Type'), f.type],
    [__('Recorded'), f.date],
    [__('Auction'), f.auction],
    [__('Case / TSN'), f.case],
    [__('Borrower'), f.borrower],
    [__('Trustee'), f.trustee],
  ].filter((row) => row[1])
})

function onTaxPull(data) {
  if (data.reference_docname === props.lead) {
    taxPulls.reload()
    open.value = true
  }
}

onMounted(() => $socket.on('crm_tax_pull', onTaxPull))
onBeforeUnmount(() => $socket.off('crm_tax_pull', onTaxPull))
</script>
