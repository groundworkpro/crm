<template>
  <!-- Why the 13 steps gave low confidence. Step 1 flags (the house's own facts) come first and,
       where a fact could fix it, take the rep's value and re-run with it. -->
  <div v-if="items.length" class="mt-2 rounded-md bg-surface-amber-1 px-2.5 py-2 text-sm text-ink-gray-8">
    <div class="text-xs font-medium text-ink-amber-7">{{ __('Why low confidence') }}</div>
    <ul class="mt-1 space-y-1">
      <li v-for="w in items" :key="w.key" class="flex gap-1.5">
        <span class="w-12 shrink-0 text-ink-gray-5">{{ __('Step {0}', [w.step]) }}</span>
        <span>{{ w.text }}</span>
      </li>
    </ul>
    <template v-if="fields.length">
      <p class="mt-2 text-xs text-ink-gray-6">
        {{ __('Step 1 flagged this house. If you know the right facts, fill them in and re-run; they are saved on the lead.') }}
      </p>
      <div class="mt-1.5 grid grid-cols-2 gap-1.5">
        <label v-for="f in fields" :key="f" class="text-xs text-ink-gray-6">
          {{ LABELS[f] }}
          <select v-if="f === 'type'" v-model="form.type" class="form-select mt-0.5 w-full text-sm">
            <option value="">{{ __('Pick…') }}</option>
            <option v-for="(l, v) in TYPES" :key="v" :value="v">{{ l }}</option>
          </select>
          <input
            v-else
            v-model="form[f]"
            type="number"
            min="0"
            :step="f === 'baths' ? 0.5 : 1"
            class="form-input mt-0.5 w-full text-sm"
            :placeholder="current[f] ? String(current[f]) : ''"
          />
        </label>
      </div>
      <Button class="mt-2" size="sm" variant="solid" :disabled="!filled || busy" :loading="busy" @click="rerun">
        {{ __('Re-run with these') }}
      </Button>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { Button } from 'frappe-ui'

const props = defineProps({
  why: { type: Array, default: () => [] }, // [{step, key, text, fields}]
  subject: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['rerun'])

const LABELS = { type: __('Home type'), sqft: __('Living sqft'), beds: __('Beds'), baths: __('Baths'), year_built: __('Year built'), lot_sqft: __('Lot sqft') }
const TYPES = {
  single_family: __('Single family'), townhouse: __('Townhouse'), condo: __('Condo'),
  multi_family: __('Multi-family'), manufactured: __('Manufactured'), land: __('Land'),
}
const items = computed(() => (props.why || []).filter((w) => w && w.text))
const fields = computed(() => [...new Set(items.value.flatMap((w) => w.fields || []))].filter((f) => LABELS[f]))
const current = computed(() => props.subject || {})
const form = reactive({ type: '' })
const filled = computed(() => fields.value.some((f) => form[f] !== undefined && form[f] !== ''))

function rerun() {
  const facts = {}
  for (const f of fields.value) if (form[f] !== undefined && form[f] !== '') facts[f] = form[f]
  emit('rerun', facts)
}
</script>
