<template>
  <Dialog v-model="show" :options="{ title: __('Property records'), size: '5xl' }">
    <template #body-content>
      <div class="flex flex-col gap-4 text-sm text-ink-gray-8">
        <!-- Tabs, same split as BatchLeads' property page. -->
        <div class="flex flex-wrap gap-1 rounded-lg bg-surface-gray-2 p-1">
          <button
            v-for="t in tabs"
            :key="t.key"
            type="button"
            class="flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium"
            :class="
              tab === t.key
                ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                : 'text-ink-gray-6 hover:text-ink-gray-8'
            "
            @click="tab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <!-- Tax Information -->
        <section v-if="tab === 'tax'">
          <h3 class="mb-3 text-base font-medium">{{ __('Tax Information') }}</h3>
          <div class="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
            <Field v-for="f in taxFields" :key="f[0]" :label="f[0]" :value="f[1]" />
          </div>
        </section>

        <!-- Sale & Loan -->
        <template v-else-if="tab === 'sale'">
          <RecordTable
            :title="__('Last sale')"
            :columns="saleColumns"
            :rows="report.last_sale ? [report.last_sale] : []"
          />
          <RecordTable
            :title="__('Last transfer')"
            :columns="transferColumns"
            :rows="report.last_transfer ? [report.last_transfer] : []"
          />
          <RecordTable
            :title="__('Transactions history')"
            :columns="saleColumns"
            :rows="report.transactions || []"
          />
          <RecordTable
            :title="__('Current loans')"
            :columns="currentLoanColumns"
            :rows="report.current_loans || []"
          />
          <div
            v-if="report.loan_totals?.open_balance || report.loan_totals?.est_payment"
            class="-mt-2 text-xs text-ink-gray-6"
          >
            <span v-if="report.loan_totals.open_balance">
              {{ __('Est. open balance') }}: {{ money(report.loan_totals.open_balance) }}
            </span>
            <span v-if="report.loan_totals.est_payment">
              · {{ __('Est. monthly payment') }}: {{ money(report.loan_totals.est_payment) }}
            </span>
          </div>
          <RecordTable
            :title="__('Loans history')"
            :columns="loanColumns"
            :rows="report.loans || []"
          />
        </template>

        <!-- Foreclosure & Lien -->
        <template v-else>
          <section>
            <h3 class="mb-3 text-base font-medium">{{ __('Foreclosure information') }}</h3>
            <div v-if="!report.foreclosures?.length" class="text-ink-gray-5">
              {{ __('No foreclosure filings on record.') }}
            </div>
            <div
              v-for="(f, i) in report.foreclosures"
              :key="i"
              class="mb-3 rounded-lg border border-outline-gray-2 p-3"
            >
              <div class="mb-2 flex items-center gap-2 text-xs font-medium">
                <span
                  v-if="f.active !== null && f.active !== undefined"
                  class="rounded px-1.5 py-0.5"
                  :class="
                    f.active ? 'bg-surface-red-2 text-ink-red-4' : 'bg-surface-gray-2 text-ink-gray-6'
                  "
                  >{{ f.active ? __('Active') : __('Not active') }}</span
                >
                <span class="text-ink-gray-6">{{ date(f.recording_date) }}</span>
              </div>
              <div class="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
                <Field v-for="row in foreclosureFields(f)" :key="row[0]" :label="row[0]" :value="row[1]" />
              </div>
            </div>
          </section>

          <section>
            <h3 class="mb-3 text-base font-medium">{{ __('Involuntary liens') }}</h3>
            <div class="mb-3 grid grid-cols-3 gap-x-6 gap-y-3 sm:grid-cols-6">
              <Field :label="__('Tax lien')" :value="yesNo(report.lien_flags?.tax_lien)" />
              <Field :label="__('Involuntary lien')" :value="yesNo(report.lien_flags?.lien)" />
              <Field :label="__('Judgment')" :value="yesNo(report.lien_flags?.judgment)" />
            </div>
            <div
              v-for="(l, i) in report.liens"
              :key="i"
              class="mb-3 grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-outline-gray-2 p-3 sm:grid-cols-3 lg:grid-cols-6"
            >
              <Field v-for="row in lienFields(l)" :key="row[0]" :label="row[0]" :value="row[1]" />
            </div>
            <div v-if="!report.liens?.length && report.source === 'RealEstateAPI'" class="text-xs text-ink-gray-5">
              {{ __('Our property data only says whether a lien exists, not the lien documents.') }}
            </div>
          </section>
        </template>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { formatNumber } from '@/utils'
import { Dialog } from 'frappe-ui'
import { computed, h, ref } from 'vue'

const props = defineProps({
  report: { type: Object, default: () => ({}) },
})

const show = defineModel()
const tab = ref('tax')

const tabs = [
  { key: 'tax', label: __('Tax Information') },
  { key: 'sale', label: __('Sale & Loan') },
  { key: 'fcl', label: __('Foreclosure & Lien') },
]

const NA = 'N/A'
const money = (v) => (v || v === 0 ? '$' + formatNumber(v) : NA)
const text = (v) => (v || v === 0 ? String(v) : NA)
const yesNo = (v) => (v === true ? __('Yes') : v === false ? __('No') : NA)
// YYYY-MM-DD → MM/DD/YYYY, the way BatchLeads and the county records print it.
const date = (v) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v || '')
  return m ? `${m[2]}/${m[3]}/${m[1]}` : NA
}
const percent = (v) => (v ? `${v}%` : NA)
const months = (v) => (v ? String(v) : NA)

const Field = (p) =>
  h('div', { class: 'min-w-0' }, [
    h('div', { class: 'text-xs font-medium text-ink-blue-3' }, p.label),
    h('div', { class: 'break-words text-ink-gray-8' }, p.value),
  ])
Field.props = ['label', 'value']

// A titled, horizontally scrollable table. columns: [label, key, formatter, bold?]
const RecordTable = (p) =>
  h('section', { class: 'min-w-0' }, [
    h('h3', { class: 'mb-2 text-sm font-semibold uppercase tracking-wide text-ink-gray-8' }, p.title),
    p.rows.length
      ? h('div', { class: 'overflow-x-auto' }, [
          h('table', { class: 'w-full min-w-[640px] text-left text-sm' }, [
            h(
              'thead',
              h(
                'tr',
                { class: 'border-b border-outline-gray-3 text-ink-gray-6' },
                p.columns.map((c) => h('th', { class: 'px-2 py-1.5 font-normal' }, c[0])),
              ),
            ),
            h(
              'tbody',
              p.rows.map((row) =>
                h(
                  'tr',
                  { class: 'border-b border-outline-gray-1' },
                  p.columns.map((c) =>
                    h(
                      'td',
                      { class: ['px-2 py-1.5 align-top', c[3] ? 'font-semibold' : ''] },
                      c[2](row[c[1]]),
                    ),
                  ),
                ),
              ),
            ),
          ]),
        ])
      : h('div', { class: 'text-ink-gray-5' }, __('None on record.')),
  ])
RecordTable.props = ['title', 'columns', 'rows']

const report = computed(() => props.report || {})

const taxFields = computed(() => {
  const t = report.value.tax || {}
  return [
    [__('Parcel #'), text(t.parcel)],
    [__('Current Owner'), text(t.owner)],
    [__('Property Tax'), money(t.property_tax)],
    [__('Tax Year'), text(t.tax_year)],
    [__('Tax Delinquent Year'), text(t.delinquent_year)],
    [__('Total Assessed Value'), money(t.total_assessed)],
    [__('Assessed Land Value'), money(t.assessed_land)],
    [__('Assessed Improvement Value'), money(t.assessed_improvement)],
    [__('Assessment Year'), text(t.assessment_year)],
    [__('Total Market Value'), money(t.total_market)],
    [__('Market Land Value'), money(t.market_land)],
    [__('Market Improvement Value'), money(t.market_improvement)],
    [__('Market Value Year'), text(t.market_value_year)],
    [__('Tax Mailing Address'), text(t.mailing)],
  ]
})

const saleColumns = [
  [__('Sale Date'), 'sale_date', date, true],
  [__('Recording Date'), 'recording_date', date],
  [__('Sale Price'), 'price', money, true],
  [__('Document No.'), 'doc_number', text],
  [__('Document Type'), 'doc_type', text],
  [__('Buyer'), 'buyers', text],
  [__('Seller'), 'sellers', text],
]
const transferColumns = saleColumns.slice(0, 5)
const currentLoanColumns = [
  [__('Recording Date'), 'recording_date', date],
  [__('Loan Type'), 'loan_type', text],
  [__('Loan Amount'), 'amount', money, true],
  [__('Lender Name'), 'lender', text],
  [__('Loan Due Date'), 'due_date', date],
  [__('Est. Loan Payment'), 'est_payment', money, true],
  [__('Est. Balance'), 'est_balance', money, true],
  [__('Interest'), 'rate', percent, true],
]
const loanColumns = [
  [__('Recording Date'), 'recording_date', date, true],
  [__('Loan Type'), 'loan_type', text],
  [__('Loan Amount'), 'amount', money, true],
  [__('Lender Name'), 'lender', text],
  [__('Loan Due Date'), 'due_date', date],
  [__('Loan Term (Months)'), 'term_months', months],
]

function foreclosureFields(f) {
  return [
    [__('Document Number'), text(f.doc_number)],
    [__('Document Type'), text(f.doc_type)],
    [__('Foreclosure Status'), text(f.status)],
    [__('Unpaid Amount'), money(f.unpaid_amount)],
    [__('Opening Bid'), money(f.opening_bid)],
    [__('Borrower Name'), text(f.borrower)],
    [__('Lender Name'), text(f.lender)],
    [__('Recording Date'), date(f.recording_date)],
    [__('Auction Date'), date(f.auction_date)],
    [__('Auction Location'), text(f.auction_location)],
    [__('Loan Default Date'), date(f.default_date)],
    [__('Original Loan Amount'), money(f.original_loan_amount)],
    [__('Trustee/Attorney Name'), text(f.trustee)],
    [__('Trustee Address'), text(f.trustee_address)],
    [__('Phone'), text(f.trustee_phone || f.lender_phone)],
    [__('Care of Name'), text(f.care_of)],
    [__('Case Number'), text(f.case_number)],
    [__('Trustee Sale #'), text(f.trustee_sale_number)],
  ]
}

function lienFields(l) {
  return [
    [__('Document Type'), text(l.doc_type)],
    [__('Document Number'), text(l.doc_number)],
    [__('Lien Amount'), money(l.lien_amount)],
    [__('Judgement Amount'), money(l.judgment_amount)],
    [__('Filing Date'), date(l.filing_date)],
    [__('Lien Recording Date'), date(l.recording_date)],
    [__('Tax Period End Date'), date(l.tax_period_end)],
  ]
}
</script>
