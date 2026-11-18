<template>
  <!-- What the subject would rent for (Dennis, 2026-11-17). Two numbers side by
       side, never blended: Zillow's Rent Zestimate, Redfin's Rental Estimate
       (through the Redfin scraper), and the median of nearby rentals in PropWarehouse (Redfin, Zillow, Trulia, Rent.com, Craigslist).
       Inside the clickable subject card, so every click here stops. -->
  <div v-if="show" class="mt-1 text-xs text-ink-gray-6" @click.stop>
    <div class="flex flex-wrap items-center gap-x-1.5">
      <span>{{ __('Rent/mo') }}</span>
      <span v-if="zillow" :title="__('Zillow Rent Zestimate')">
        {{ __('Zillow') }} <b class="text-ink-gray-8">{{ money(zillow) }}</b>
      </span>
      <template v-if="redfin">
        <span v-if="zillow" class="text-ink-gray-4">·</span>
        <span :title="redfinTitle">
          {{ __('Redfin') }} <b class="text-ink-gray-8">{{ money(redfin.rent) }}</b>
        </span>
      </template>
      <span v-if="(zillow || redfin) && (near?.count || loading)" class="text-ink-gray-4">·</span>
      <span v-if="loading" class="text-ink-gray-4">{{ __('nearby…') }}</span>
      <button
        v-else-if="near?.count"
        class="rounded hover:underline"
        :title="nearTitle"
        @click.stop="open = !open"
      >
        {{ __('Nearby') }} <b class="text-ink-gray-8">{{ money(near.median) }}</b>
        <span class="text-ink-gray-5">
          ({{ near.count }}<template v-if="near.count > 1">, {{ money(near.low) }}–{{ money(near.high) }}</template>)
        </span>
        <FeatherIcon :name="open ? 'chevron-up' : 'chevron-down'" class="inline size-3" />
      </button>
      <span v-else-if="!zillow && !redfin" class="text-ink-gray-4">{{ __('no estimate') }}</span>
    </div>
    <div v-if="open && near?.count" class="mt-1 rounded border border-outline-gray-2 bg-surface-white">
      <div class="border-b border-outline-gray-1 px-2 py-1 text-2xs text-ink-gray-5">
        {{ nearWhat }}
      </div>
      <div
        v-for="(l, i) in near.listings"
        :key="i"
        class="flex items-baseline gap-2 border-b border-outline-gray-1 px-2 py-1 last:border-b-0"
      >
        <div class="min-w-0 flex-1">
          <a
            v-if="l.url"
            :href="l.url"
            target="_blank"
            rel="noopener"
            class="block truncate text-ink-gray-8 hover:underline"
            :title="l.address"
          >{{ l.address }}</a>
          <div v-else class="truncate text-ink-gray-8" :title="l.address">{{ l.address }}</div>
          <div class="text-2xs text-ink-gray-5">
            {{ rowFacts(l) }}
          </div>
        </div>
        <b class="shrink-0 text-ink-gray-8">{{ money(l.rent) }}</b>
      </div>
    </div>
  </div>
</template>

<script setup>
import { FeatherIcon, call } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  subject: { type: Object, default: null },
})

const near = ref(null)
const redfin = ref(null)
const loading = ref(false)
const open = ref(false)

const zillow = computed(() => Number(props.subject?.rent_zestimate) || 0)
const show = computed(() => !!zillow.value || !!redfin.value || loading.value || near.value)

const redfinTitle = computed(() => {
  const r = redfin.value || {}
  const range = r.low && r.high ? ` (${money(r.low)} – ${money(r.high)})` : ''
  return __('Redfin Rental Estimate {0}{1}', [money(r.rent), range])
})

// Only exact facts filter the rentals: a seller's "1000 - 2000" band would
// pretend to a precision the answer does not have.
const query = computed(() => {
  const s = props.subject
  if (!s || s.lat == null || s.lng == null) return null
  return {
    lat: s.lat,
    lng: s.lng,
    beds: s.beds_exact ? s.beds : null,
    sqft: s.sqft_exact ? s.sqft : null,
    property_type: s.property_type || null,
    redfin_property_id: s.redfin_property_id || null,
  }
})

let seq = 0
watch(
  () => JSON.stringify(query.value),
  async () => {
    const q = query.value
    near.value = null
    redfin.value = null
    open.value = false
    if (!q) return
    const mine = ++seq
    loading.value = true
    try {
      const out = await call('crm.api.rent_estimate.get_rent_estimate', q)
      if (mine === seq) {
        near.value = out?.listings || null
        redfin.value = out?.redfin?.rent ? out.redfin : null
      }
    } catch (e) {
      if (mine === seq) {
        near.value = null
        redfin.value = null
      }
    } finally {
      if (mine === seq) loading.value = false
    }
  },
  { immediate: true },
)

const nearWhat = computed(() => {
  const n = near.value || {}
  const like = []
  if (n.filtered && n.beds) like.push(__('{0} bd ±1', [n.beds]))
  if (n.filtered && n.sqft) like.push(__('{0} sqft ±30%', [Math.round(n.sqft).toLocaleString()]))
  const bits = [
    __('{0} rentals within {1} mi', [n.count, n.radius_mi]),
    like.length ? __('like this house ({0})', [like.join(', ')]) : '',
    n.loose ? __('too few like this house, so all rentals') : '',
    n.by_sqft ? __('{0}/sf → {1}', [`$${n.per_sqft}`, money(n.by_sqft)]) : '',
  ]
  return bits.filter(Boolean).join(' · ')
})

const nearTitle = computed(() => {
  const n = near.value || {}
  return [
    __('Median rent of nearby rentals {0}', [money(n.median)]),
    n.count > 1 ? __('Middle half {0} – {1}', [money(n.low), money(n.high)]) : '',
    nearWhat.value,
    __('Listed and recently listed, from Redfin, Zillow, Trulia, Rent.com and Craigslist'),
  ]
    .filter(Boolean)
    .join('\n')
})

function rowFacts(l) {
  const bits = []
  if (l.beds != null) bits.push(__('{0} bd', [l.beds]))
  if (l.baths != null) bits.push(__('{0} ba', [l.baths]))
  if (l.sqft) bits.push(__('{0} sqft', [l.sqft.toLocaleString()]))
  bits.push(__('{0} mi', [l.dist_mi]))
  if (l.status === 'delisted') bits.push(__('no longer listed'))
  if (l.sources?.length) bits.push(l.sources.map(sourceName).join(' + '))
  return bits.join(' · ')
}

const SOURCE_NAMES = {
  redfin: 'Redfin',
  zillow: 'Zillow',
  trulia: 'Trulia',
  rentcom: 'Rent.com',
  craigslist: 'Craigslist',
}
function sourceName(s) {
  return SOURCE_NAMES[s] || s
}

function money(v) {
  const n = Number(v)
  return n ? '$' + Math.round(n).toLocaleString() : '—'
}
</script>
