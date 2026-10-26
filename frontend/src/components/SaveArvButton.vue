<template>
  <!-- "Save ARV" on a property-pricer (Ask AI) result: sets the lead's ARV field
       and logs it on the timeline (crm.api.arv.save_arv). -->
  <button
    v-if="lead && arv > 0"
    type="button"
    class="inline-flex h-7 items-center gap-1.5 rounded bg-surface-gray-2 px-2 text-base text-ink-gray-8 hover:bg-surface-gray-3 disabled:opacity-60"
    :disabled="saving || savedArv === arv"
    @click="save"
  >
    <FeatherIcon :name="savedArv === arv ? 'check' : 'save'" class="h-3.5 w-3.5" />
    {{ savedArv === arv ? __('ARV saved') : saving ? __('Saving…') : __('Save ARV to lead') }}
  </button>
</template>

<script setup>
import { ref } from 'vue'
import { FeatherIcon, call, toast } from 'frappe-ui'

const props = defineProps({
  lead: { type: String, default: '' },
  arv: { type: Number, default: 0 },
  arvLow: { type: Number, default: null },
  arvHigh: { type: Number, default: null },
  offer: { type: Number, default: null },
})
const emit = defineEmits(['saved'])

const saving = ref(false)
const savedArv = ref(null)

async function save() {
  saving.value = true
  try {
    const res = await call('crm.api.arv.save_arv', {
      lead: props.lead,
      arv: props.arv,
      arv_low: props.arvLow,
      arv_high: props.arvHigh,
      offer: props.offer,
    })
    savedArv.value = props.arv
    toast.success(res.stored ? __('ARV saved to the lead') : __('ARV logged on the timeline only'))
    emit('saved', res)
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not save the ARV.'))
  } finally {
    saving.value = false
  }
}
</script>
