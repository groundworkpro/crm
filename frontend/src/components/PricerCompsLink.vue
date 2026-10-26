<template>
  <!-- A plain link, not window.open: browsers never block a real anchor click,
       and cmd/middle-click work for free. New tab so the AI chat stays open. -->
  <a
    v-if="href"
    :href="href"
    target="_blank"
    rel="noopener"
    class="inline-flex h-7 items-center gap-1.5 rounded bg-surface-gray-2 px-2 text-base text-ink-gray-8 hover:bg-surface-gray-3"
    :title="__('Open this lead\'s comps page to add repairs and the rest of the offer math')"
  >
    <FeatherIcon name="external-link" class="h-3.5 w-3.5" />
    {{ __('Finish on comps page') }}
  </a>
</template>

<script setup>
// Quick link from a property-pricer (Ask AI) result to the lead's comps page,
// where the rep can put in repairs and the rest of the cash-offer calcs.
import { computed } from 'vue'
import { FeatherIcon } from 'frappe-ui'
import { compsPagePath } from '@/utils/comps'

const props = defineProps({
  // CRM Lead name (or a scratch CRM Property name -- compsPagePath handles both).
  lead: { type: String, default: '' },
  // Ask AI job id: the comps page picks the AI's comps and fills its numbers.
  job: { type: String, default: '' },
})

const href = computed(() => {
  const path = compsPagePath(props.lead)
  return path && props.job ? `${path}?ai=${props.job}` : path
})
</script>
