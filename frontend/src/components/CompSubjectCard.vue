<template>
  <!-- Clickable for the same reason the pin is: every comp beneath this opens a
       gallery, and the subject is the house actually being priced. Consistency
       matters more than novelty here -- a card that looks like the others and
       does not behave like them is the surprising one. -->
  <div
    class="cursor-pointer border-b-2 border-outline-blue-2 bg-surface-blue-1 transition-colors hover:bg-surface-blue-2"
    :title="__('Photos & details for this property')"
    @click="$emit('open')"
  >
    <div class="relative aspect-[3/2] w-full overflow-hidden bg-surface-gray-2">
      <!-- Deliberately NOT `loading="lazy"`: this is the first card in the tray,
           always on screen, so there was never anything to defer -- and lazy does
           not fire inside this scroller anyway (see the note in CompTrayCard),
           which is why the subject's photo only appeared after a hover. -->
      <img
        v-if="photo && !broken"
        :src="photo"
        :alt="address"
        decoding="async"
        referrerpolicy="no-referrer"
        class="size-full object-cover"
        @error="broken = true"
      />
      <div
        v-else
        class="flex size-full flex-col items-center justify-center gap-1 text-ink-gray-4"
      >
        <FeatherIcon name="home" class="size-6" />
        <span class="text-2xs">{{ __('No photo') }}</span>
      </div>
      <span
        class="absolute left-2 top-2 rounded px-1.5 py-0.5 text-2xs font-semibold text-white shadow-sm"
        :style="{ background: SUBJECT }"
      >
        {{ __('This property') }}
      </span>
      <button
        v-if="subject?.lat != null && subject?.lng != null"
        class="absolute right-2 top-2 z-30 rounded bg-surface-white/80 px-1.5 py-1 text-ink-gray-6 shadow-sm ring-1 ring-outline-gray-2 transition hover:bg-surface-white hover:text-ink-gray-9"
        :title="__('Street View')"
        @click.stop="$emit('street')"
      >
        <FeatherIcon name="navigation" class="size-3.5" />
      </button>
    </div>

    <div class="px-3 py-2">
      <div class="truncate text-sm font-semibold text-ink-gray-9" :title="address">
        {{ address }}
      </div>
      <div class="mt-0.5 flex flex-wrap items-center gap-x-1 text-xs text-ink-gray-7">
        <span>{{ facts }}</span>
        <!-- Zillow's sqft is sometimes simply wrong about the one house being
             priced, and everything downstream (filter ladder, ± deltas, repair
             totals) keys off it — so the correction lives right on the fact.
             Hover-quiet pencil, same grammar as the kanban field edits. -->
        <button
          v-if="canEditSqft && !editingSqft"
          class="rounded p-0.5 text-ink-gray-4 transition hover:bg-surface-gray-2 hover:text-ink-gray-8"
          :title="manualSqft ? __('Edit square footage (set manually)') : __('Edit square footage')"
          @click.stop="startSqftEdit"
        >
          <FeatherIcon name="edit-2" class="size-3" />
        </button>
        <span v-if="manualSqft && !editingSqft" class="rounded bg-surface-gray-2 px-1 text-2xs text-ink-gray-6" :title="__('Square footage was set manually and overrides Zillow/listing data')">
          {{ __('Manual sqft') }}
        </span>
      </div>
      <div v-if="editingSqft" class="mt-1 flex items-center gap-1" @click.stop>
        <input
          ref="sqftInput"
          v-model="sqftDraft"
          type="text"
          inputmode="numeric"
          :placeholder="__('sqft')"
          class="h-6 w-20 rounded border border-outline-gray-2 bg-surface-white px-1.5 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
          @keydown.enter.prevent="saveSqft"
          @keydown.esc.prevent="cancelSqftEdit"
        />
        <Button size="sm" variant="solid" :label="__('Save')" @click.stop="saveSqft" />
        <Button
          v-if="manualSqft"
          size="sm"
          variant="subtle"
          :label="__('Reset')"
          :title="__('Clear the manual value and go back to Zillow/listing data')"
          @click.stop="clearSqft"
        />
        <Button size="sm" variant="ghost" icon="x" @click.stop="cancelSqftEdit" />
      </div>
      <!-- Sources disagreeing about the house is exactly what a rep about to
           price off these numbers needs shoved in front of them. -->
      <CompDiscrepancyFlag :check="subject?.redfin_check || null" />
      <!-- What it actually SOLD for is a verified transaction and outranks every
           other number here, so it is stated separately rather than folded in
           with the estimates. -->
      <!-- Zillow often records that a house sold and when, without a price (a
           non-disclosure state, or simply an unpriced public record). "Last sold
           — · Jun 3, 2021" reads like a rendering fault; the date on its own is a
           real fact and is worth saying. -->
      <div v-if="lastSale && (lastSale.price || lastSale.date)" class="mt-1 text-xs text-ink-gray-6">
        {{ __('Last sold') }}
        <b v-if="lastSale.price" class="text-ink-gray-8">{{ money(lastSale.price) }}</b>
        <template v-if="lastSale.price && lastSale.date"> · </template>
        <template v-if="lastSale.date">{{ fmtDate(lastSale.date) }}</template>
      </div>
      <!-- ONE value, unlabelled (Lance): the first of Zestimate → Realtor →
           Redfin → assessed value that exists. They are fallbacks for each
           other, not a panel to compare; the source rides in the tooltip. -->
      <div v-if="estimate" class="mt-1 text-xs text-ink-gray-6" :title="estimate.title">
        <a
          v-if="estimate.href"
          :href="estimate.href"
          target="_blank"
          rel="noopener"
          class="font-semibold text-ink-gray-8 hover:underline"
          @click.stop
        >{{ money(estimate.value) }}</a>
        <b v-else class="text-ink-gray-8">{{ money(estimate.value) }}</b>
      </div>
      <CompRentEstimate :subject="subject" />
    </div>
  </div>
</template>

<script setup>
/**
 * The subject, rendered in the same card grammar as the comps directly beneath
 * it — so "is this one bigger or smaller than mine" is a glance rather than a
 * memory test. Blue and heavier-bordered so it never reads as one of them.
 */
import { Button, FeatherIcon } from 'frappe-ui'
import { computed, nextTick, ref, watch } from 'vue'
import CompDiscrepancyFlag from '@/components/CompDiscrepancyFlag.vue'
import CompRentEstimate from '@/components/CompRentEstimate.vue'
import { COMP_COLORS, formatLotSize } from '@/utils/comps'

const props = defineProps({
  subject: { type: Object, default: null },
  address: { type: String, default: '' },
  // Off in practice runs: the override is a team-wide fact write on the lead.
  canEditSqft: { type: Boolean, default: false },
})
const emit = defineEmits(['open', 'street', 'saveSqft'])

// --- manual sqft override ------------------------------------------------
const editingSqft = ref(false)
const sqftDraft = ref('')
const sqftInput = ref(null)
const manualSqft = computed(() => props.subject?.source?.sqft === 'manual')

function startSqftEdit() {
  const s = props.subject || {}
  // Seed with the exact number when there is one; a band seeds blank rather
  // than a midpoint the source never named.
  sqftDraft.value = s.sqft_exact && s.sqft ? String(Math.round(s.sqft)) : ''
  editingSqft.value = true
  nextTick(() => sqftInput.value?.focus())
}

function cancelSqftEdit() {
  editingSqft.value = false
}

function saveSqft() {
  const n = Math.round(Number(String(sqftDraft.value).replace(/[^0-9.]/g, '')))
  if (!Number.isFinite(n) || n <= 0) return
  editingSqft.value = false
  emit('saveSqft', n)
}

function clearSqft() {
  editingSqft.value = false
  emit('saveSqft', null)
}

const SUBJECT = COMP_COLORS.subject.bg
const broken = ref(false)

const photo = computed(() => props.subject?.cover_photo || '')
watch(photo, () => {
  broken.value = false
})

const facts = computed(() => {
  const s = props.subject || {}
  const bits = []
  // `*_label` is the interval the SOURCE named ("1000 - 2000"), never a midpoint:
  // a vague seller answer must not be rendered as precision it does not have.
  if (s.beds_label) bits.push(__('{0} bd', [s.beds_label]))
  if (s.baths_label) bits.push(__('{0} ba', [s.baths_label]))
  if (s.sqft_label) bits.push(__('{0} sqft', [s.sqft_label]))
  const lot = formatLotSize(s.lot_size, { compact: true })
  if (lot) bits.push(lot)
  // Year was missing here while every comp card beneath it showed one, so the
  // one row you compare the others against was the row you could not compare on
  // age. "built" spelled out, matching the comp cards exactly.
  if (s.year_built_label) bits.push(__('built {0}', [s.year_built_label]))
  if (s.property_type) bits.push(s.property_type)
  return bits.join(' · ') || __('No details on file')
})

const lastSale = computed(() => props.subject?.last_sale || null)

// First available of Zestimate → Realtor → Redfin → assessed. One number on
// the card; the tooltip says which it is (and, for Realtor, the range and the
// other two AVMs).
const estimate = computed(() => {
  const s = props.subject || {}
  if (Number(s.zestimate) > 0) {
    return { value: s.zestimate, title: __('Zestimate {0}', [money(s.zestimate)]) }
  }
  const r = s.realtor_estimate
  if (r && Number(r.value) > 0) {
    const lines = [__('Realtor estimate {0}', [money(r.value)])]
    if (r.low && r.high) lines.push(__('Range {0} – {1}', [money(r.low), money(r.high)]))
    if (r.source) lines.push(__('Model: {0}', [r.source]))
    for (const a of r.all || []) {
      if (a?.estimate && a.name !== r.source) lines.push(`${a.name}: ${money(a.estimate)}`)
    }
    if (r.as_of) lines.push(__('As of {0}', [fmtDate(r.as_of)]))
    lines.push(__('(no Zestimate for this property)'))
    return { value: r.value, title: lines.join('\n'), href: r.href || '' }
  }
  if (Number(s.redfin_estimate) > 0) {
    return {
      value: s.redfin_estimate,
      title: __('Redfin Estimate {0} (no Zestimate or Realtor estimate)', [money(s.redfin_estimate)]),
    }
  }
  if (Number(s.assessed_value) > 0) {
    return {
      value: s.assessed_value,
      title: __('Tax assessed value {0} (no Zillow / Realtor / Redfin estimate)', [money(s.assessed_value)]),
    }
  }
  return null
})

function money(v) {
  const n = Number(v)
  return n ? '$' + Math.round(n).toLocaleString() : '—'
}

function fmtDate(v) {
  if (!v) return ''
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/)
  const d = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v)
  if (isNaN(d)) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
</script>
