<template>
  <div class="border-b border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-xs text-ink-gray-7">
    <div class="flex items-center gap-1.5">
      <FeatherIcon name="zap" class="size-3.5 text-ink-gray-6" />
      <span class="font-medium text-ink-gray-8">{{ __('Ask AI') }}</span>
      <span class="ml-auto text-sm font-semibold text-ink-gray-9">{{ money(run.arv) }}</span>
    </div>
    <div class="mt-0.5 text-ink-gray-5">
      {{ __('ARV {0}–{1} · {2} comps picked · repairs {3}', [money(run.arv_low), money(run.arv_high), usedCount, money(run.offers?.repairs)]) }}
    </div>
    <div v-if="missing.length" class="mt-1 text-ink-gray-5">
      {{ __('Not on this map:') }}
      <span v-for="(m, i) in missing" :key="m.address" :title="m.why">
        {{ m.address }} ({{ money(m.price) }}){{ i < missing.length - 1 ? ', ' : '' }}
      </span>
    </div>
    <p v-if="run.would_change_it" class="mt-1 line-clamp-2 text-ink-gray-5" :title="run.would_change_it">
      {{ __('Would change it:') }} {{ run.would_change_it }}
    </p>
  </div>
</template>

<script setup>
import { FeatherIcon } from 'frappe-ui'

defineProps({
  run: { type: Object, required: true },
  usedCount: { type: Number, default: 0 },
  missing: { type: Array, default: () => [] },
})

const money = (v) => (Number(v) ? '$' + Math.round(Number(v)).toLocaleString() : '—')
</script>
