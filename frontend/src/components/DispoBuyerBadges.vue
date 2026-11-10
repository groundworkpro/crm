<template>
  <span v-if="badges.length" class="inline-flex flex-wrap items-center gap-1">
    <span
      v-for="b in badges"
      :key="b.buyer"
      :title="tooltip(b)"
      class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none"
      :class="chipClass(b)"
    >
      <component :is="MARKS[b.buyer] || KeyGleeMark" class="h-2.5 w-auto" />
      <span class="max-w-20 truncate">{{ label(b) }}</span>
    </span>
  </span>
</template>

<script setup>
import NewWesternMark from '@/components/Icons/NewWesternMark.vue'
import KeyGleeMark from '@/components/Icons/KeyGleeMark.vue'
import EzReiMark from '@/components/Icons/EzReiMark.vue'
import { createResource } from 'frappe-ui'
import { computed, watch } from 'vue'

const MARKS = { nw: NewWesternMark, kg: KeyGleeMark, ez: EzReiMark }

const props = defineProps({
  // The server's `_dispo_buyers` pseudo-field: null when no buyer covers this
  // lead's area, so most cards render nothing at all.
  value: { type: [Array, Object, String], default: null },
  // Lead page (and Today modal) have a location, not a precomputed field.
  // Opt-in so the Kanban — which already ships `value` and mounts this once
  // per card — never creates a resource per row.
  fetch: { type: Boolean, default: false },
  city: { type: String, default: '' },
  state: { type: String, default: '' },
  county: { type: String, default: '' },
})

const fetched = props.fetch
  ? createResource({
      url: 'crm.api.dispo_buyers.get_dispo_buyers',
      auto: false,
    })
  : null

if (fetched) {
  watch(
    () => [props.city, props.state, props.county],
    ([city, state, county]) => {
      if (!city && !state && !county) {
        fetched.data = null
        return
      }
      fetched.submit({ city: city || '', state: state || '', county: county || '' })
    },
    { immediate: true },
  )
}

const NO = 'No'

// Compact the resolve() dict into the same badge list `summary()`
// ships. Needed because prod still returns resolve() until this backend
// deploys, and the Kanban already passes the compact array.
function badgesFromResolve(r) {
  if (!r || typeof r !== 'object') return []
  const badges = []
  if (r.new_western && r.new_western !== NO) {
    badges.push({
      buyer: 'nw',
      market: r.new_western_market,
      strong: r.new_western === 'Yes - NW city' || r.new_western === 'Yes - NW market',
      status: r.new_western,
    })
  }
  if (r.keyglee && r.keyglee !== NO) {
    badges.push({
      buyer: 'kg',
      market: r.keyglee_market,
      strong: r.keyglee === 'Yes - KG operating',
      status: r.keyglee,
    })
  }
  if (r.ezrei && r.ezrei !== NO) {
    badges.push({
      buyer: 'ez',
      market: r.ezrei_market,
      strong: true,
      status: r.ezrei,
      rank: r.ezrei_rank,
    })
  }
  return badges
}

function parseValue(v) {
  if (!v) return []
  if (Array.isArray(v)) return v
  // Kanban rows arrive through parseRows, which can stringify a value on the
  // way. Tolerate both rather than depending on which path fed us.
  if (typeof v === 'string') {
    try {
      const parsed = JSON.parse(v)
      return parseValue(parsed)
    } catch {
      return []
    }
  }
  if (typeof v === 'object' && (v.new_western || v.keyglee || v.ezrei))
    return badgesFromResolve(v)
  return []
}

const badges = computed(() => {
  if (fetched) return parseValue(fetched.data)
  return parseValue(props.value)
})

// FILLED = the company's own claim about where it buys. OUTLINED = ours.
//
// The two levels stay visually distinct on purpose, which is the same rule the
// source data keeps: New Western publishes a city list, and a lead merely in the
// same county as a listed city is us guessing at their metro buy box. One badge
// for both would quietly promote a guess to a fact.
// ezREIdispo is the exception to the second half of that rule: it has no weak
// tier at all, because nothing about their coverage is our inference. They sent
// a ranked list of counties, so the badge is either there or it isn't.
//
// Colors live in the <style> block: the light tints were near-white, which in
// dark mode left two bright pills glaring off an otherwise dark card. This app
// switches themes with [data-theme='dark'], which Tailwind's `dark:` ignores.
function chipClass(b) {
  if (!b.strong) return 'border border-outline-gray-2 text-ink-gray-5'
  return `dispo-${b.buyer === 'nw' || b.buyer === 'ez' ? b.buyer : 'kg'}`
}

function label(b) {
  if (b.buyer === 'kg' && !b.strong) return `${b.market} sold out`
  if (b.buyer === 'nw' && !b.strong) return `${b.market} area`
  // Their rank, not their market: the market here is the lead's own county,
  // which the card already prints. The rank is the thing that is theirs.
  if (b.buyer === 'ez') return b.rank ? `#${b.rank}` : 'velocity'
  return b.market
}

// Say which kind of claim this is, in words, because the colour alone cannot.
function tooltip(b) {
  const market = b.market || ''
  if (b.buyer === 'nw') {
    if (b.status === 'Yes - NW city')
      return __('New Western lists this city in their {0} market.', [market])
    if (b.status === 'Yes - NW market')
      return __(
        'New Western runs a {0} office covering this area, though this city is not on their published list.',
        [market],
      )
    return __(
      'New Western does not list this city, but they buy elsewhere in this county ({0} market). Our inference, not their claim.',
      [market],
    )
  }
  if (b.buyer === 'ez') {
    return b.rank
      ? __(
          "{0} is on ezREIdispo's EZ Velocity market list, ranked #{1} of 62. That list is the whole of their claim — they publish no map, and neighbouring counties are not inferred from it.",
          [market, String(b.rank)],
        )
      : __(
          "{0} is on ezREIdispo's EZ Velocity market list. That list is the whole of their claim — they publish no map, and neighbouring counties are not inferred from it.",
          [market],
        )
  }
  if (b.status === 'Sold out - KG')
    return __(
      "KeyGlee's {0} territory is sold out — the area is taken, but no franchise is trading there today.",
      [market],
    )
  return __(
    'KeyGlee has an operating franchise in {0}, which covers this county. Their territory is drawn wider than the metro, so this is the conservative read.',
    [market],
  )
}
</script>

<style scoped>
.dispo-nw { background: #fbf6ec; color: #8a6a33; }
.dispo-ez { background: #fdf1f0; color: #8f1817; }
.dispo-kg { background: #ecf7fa; color: #0f6f87; }

/* Dark: a faint wash of each brand color, with the brand color lifted so the
   mark still reads. Translucent so it sits on the amber/red card tints too. */
[data-theme='dark'] .dispo-nw { background: rgb(201 160 90 / 0.16); color: #e2c48c; }
[data-theme='dark'] .dispo-ez { background: rgb(229 83 75 / 0.16); color: #f4a29c; }
[data-theme='dark'] .dispo-kg { background: rgb(56 176 206 / 0.16); color: #8ed4e6; }
</style>
