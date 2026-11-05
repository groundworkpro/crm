<template>
  <!-- Ask AI's comps, apart from the board below, so a rep can see at a glance
       which of the AI's comps they also picked and which they did not. -->
  <section class="border-b border-outline-gray-2 bg-surface-gray-1">
    <button
      type="button"
      class="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs"
      @click="open = !open"
    >
      <FeatherIcon name="zap" class="size-3.5 shrink-0 text-ink-gray-6" />
      <span class="font-medium text-ink-gray-8">{{ __("Ask AI's comps") }}</span>
      <span class="text-ink-gray-5">· {{ list.length }}</span>
      <span class="ml-auto truncate text-ink-gray-6" :title="matchTitle">{{ matchLine }}</span>
      <FeatherIcon :name="open ? 'chevron-up' : 'chevron-down'" class="size-3.5 shrink-0 text-ink-gray-5" />
    </button>
    <template v-if="open">
      <div v-if="byLine" class="-mt-1 px-3 pb-1 text-2xs text-ink-gray-5">{{ byLine }}</div>
      <ul>
        <li
          v-for="a in list"
          :key="a.key"
          class="flex cursor-pointer items-start gap-2 border-t border-outline-gray-1 px-3 py-1.5 text-xs hover:bg-surface-gray-2"
          :class="active === a.key ? 'bg-surface-gray-2' : ''"
          :title="a.why || ''"
          @mouseenter="emit('hover', a.key)"
          @mouseleave="emit('hover', null)"
          @click="emit('focus', a)"
        >
          <span
            class="mt-0.5 w-16 shrink-0 whitespace-nowrap rounded text-center text-2xs font-semibold leading-4"
            :title="STATE[a.state].tip"
            :class="STATE[a.state].cls"
          >{{ STATE[a.state].label }}</span>
          <div class="min-w-0 flex-1">
            <div class="flex items-baseline gap-1.5">
              <span class="truncate font-medium text-ink-gray-8">{{ street(a.address) }}</span>
              <span class="ml-auto shrink-0 font-semibold tabular-nums text-ink-gray-9">{{ money(a.adjusted || a.price) }}</span>
            </div>
            <div class="truncate text-ink-gray-5">
              <span v-if="a.role" class="capitalize">{{ a.role }}</span>
              <span v-if="a.role && (a.adjusted || a.dist_mi != null)"> · </span>
              <span v-if="a.adjusted && a.price && a.adjusted !== a.price">{{ __('sold {0}', [money(a.price)]) }}</span>
              <span v-if="a.adjusted && a.price && a.adjusted !== a.price && a.dist_mi != null"> · </span>
              <span v-if="a.dist_mi != null">{{ Number(a.dist_mi).toFixed(2) }} mi</span>
              <span v-if="a.state === 'off_board' && !hasPoint(a)"> · {{ __('no map point') }}</span>
            </div>
            <div v-if="a.why" class="line-clamp-2 text-ink-gray-5">{{ a.why }}</div>
          </div>
        </li>
      </ul>
      <div v-if="repOnly.length" class="border-t border-outline-gray-1 px-3 py-1.5 text-xs text-ink-gray-6">
        <span class="font-medium text-ink-gray-7">{{ __('Only you picked:') }}</span>
        {{ repOnly.map((c) => street(c.address)).join(', ') }}
      </div>
      <div v-if="unpicked.length" class="border-t border-outline-gray-1 px-3 py-1.5">
        <button
          type="button"
          class="rounded bg-surface-white px-2 py-0.5 text-xs font-medium text-ink-gray-8 ring-1 ring-outline-gray-2 hover:bg-surface-gray-2"
          :disabled="busy"
          :title="__('Add the AI comps that are on this board to your picks')"
          @click="usePicks"
        >{{ busy ? __('Adding…') : __('Add AI picks to mine ({0})', [unpicked.length]) }}</button>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { FeatherIcon } from 'frappe-ui'
import { streetAddress } from '@/utils/comps'

const props = defineProps({
  run: { type: Object, required: true },
  list: { type: Array, required: true },
  repOnly: { type: Array, default: () => [] },
  active: { type: String, default: null },
  pick: { type: Function, required: true },
})
const emit = defineEmits(['hover', 'focus'])

const open = ref(true)
const busy = ref(false)

const STATE = {
  both: { label: __('Both'), tip: __('You picked it too'), cls: 'bg-surface-green-2 text-ink-green-3' },
  unpicked: { label: __('AI only'), tip: __('On your board, not in your picks'), cls: 'bg-surface-amber-2 text-ink-amber-3' },
  discarded: { label: __('Discarded'), tip: __('You discarded this one'), cls: 'bg-surface-red-2 text-ink-red-4' },
  off_board: { label: __('Off board'), tip: __('Not on your board: outside the filters, or not found'), cls: 'bg-surface-gray-3 text-ink-gray-7' },
}

const both = computed(() => props.list.filter((a) => a.state === 'both').length)
const unpicked = computed(() => props.list.filter((a) => a.state === 'unpicked'))
const matchLine = computed(() => __('{0} of {1} in your picks', [both.value, props.list.length]))
const matchTitle = computed(() =>
  props.repOnly.length ? __('You also picked {0} the AI did not use', [props.repOnly.length]) : '',
)
const byLine = computed(() => {
  const bits = [__('ARV {0}', [money(props.run.arv)])]
  if (props.run.arv_low && props.run.arv_high) bits.push(`${money(props.run.arv_low)}–${money(props.run.arv_high)}`)
  if (props.run.confidence) bits.push(__('{0} confidence', [props.run.confidence]))
  if (props.run.user) bits.push(__('run by {0}', [props.run.user]))
  if (props.run.when) bits.push(String(props.run.when).slice(0, 10))
  return bits.join(' · ')
})

const hasPoint = (a) => (a.lat != null && a.lng != null) || (a.comp?.lat != null && a.comp?.lng != null)
const street = (s) => streetAddress(s || '') || s
const money = (v) => (Number(v) ? '$' + Math.round(Number(v)).toLocaleString() : '—')

async function usePicks() {
  busy.value = true
  try {
    // One at a time: set_comp_state is read-modify-write on the lead's list.
    for (const a of unpicked.value) await props.pick(a.comp.name)
  } finally {
    busy.value = false
  }
}
</script>
