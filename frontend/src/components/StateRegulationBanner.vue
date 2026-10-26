<template>
  <!-- Wholesaling-regulation warning for the lead's state, from Titanium's
       state guide (crm/api/state_regulations.py). Renders nothing for states
       the guide has no rules or forms for. -->
  <div v-if="reg" class="border-t px-5 py-3">
    <div
      class="rounded-md px-3 py-2.5 text-sm"
      :class="loud ? 'bg-surface-red-1 text-ink-red-3' : 'bg-surface-amber-1 text-ink-amber-3'"
    >
      <div class="flex items-start gap-2">
        <FeatherIcon name="alert-triangle" class="mt-0.5 size-4 shrink-0" />
        <div class="min-w-0 flex-1">
          <div class="font-medium">
            {{ __('{0} has wholesaling regulations', [reg.name]) }}
          </div>
          <div v-if="reg.severity_label" class="mt-0.5 text-xs opacity-80">
            {{ __(reg.severity_label) }}<template v-if="reg.documents?.length">
              · {{ reg.documents.length === 1 ? __('1 state form') : __('{0} state forms', [reg.documents.length]) }}</template>
          </div>
          <p v-if="summary" class="mt-1.5 line-clamp-3 text-xs leading-relaxed">
            {{ summary }}
          </p>
          <div class="mt-1.5 text-xs font-medium">
            <a
              :href="reg.url"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-1 underline underline-offset-2"
            >
              {{ __('See regulations') }}
              <FeatherIcon name="external-link" class="size-3" />
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { FeatherIcon, createResource } from 'frappe-ui'
import { computed, watch } from 'vue'

const props = defineProps({
  lead: { type: String, required: true },
  // Passed so editing the property state re-checks the guide.
  state: { type: String, default: '' },
})

const res = createResource({
  url: 'crm.api.state_regulations.get_lead_regulation',
  makeParams: () => ({ lead: props.lead }),
  auto: true,
})

watch(
  () => [props.lead, props.state],
  () => res.reload(),
)

const reg = computed(() => res.data || null)
const loud = computed(() => ['Severe', 'High'].includes(reg.value?.severity))
const summary = computed(() => (reg.value?.regulations || '').trim())
</script>
