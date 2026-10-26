<template>
  <Dialog v-model="show" :options="{ title: __('Disagree with this ARV?') }">
    <template #body-content>
      <DialogDescription class="sr-only">
        {{ __('Send feedback on the AI after-repair value to the person who owns the pricer.') }}
      </DialogDescription>
      <div class="flex flex-col gap-4">
        <p class="text-sm leading-relaxed text-ink-gray-7">
          {{ __('Tell {0} what looks wrong. It goes to him as a message from you, with the AI\'s numbers attached, so he can fix the pricer.', [recipientName]) }}
        </p>

        <div class="rounded-lg bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-7">
          <div v-if="address" class="truncate font-medium text-ink-gray-9">{{ address }}</div>
          <div>{{ __('AI ARV: {0}', [money(arv)]) }}</div>
        </div>

        <div>
          <div class="mb-1.5 text-xs text-ink-gray-5">{{ __('What is off?') }}</div>
          <Textarea
            ref="feedbackInput"
            v-model="feedback"
            :rows="4"
            :placeholder="__('Comp 2 is a full flip, this house needs a roof…')"
            @keydown="onKeydown"
          />
        </div>

        <div>
          <div class="mb-1.5 text-xs text-ink-gray-5">{{ __('Your ARV (optional)') }}</div>
          <FormControl v-model="userArv" type="number" :placeholder="__('e.g. 160000')" />
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex w-full items-center gap-2">
        <Button :label="__('Cancel')" :disabled="sending" @click="show = false" />
        <Button
          class="ml-auto"
          variant="solid"
          :label="__('Send to {0}', [recipientName])"
          :loading="sending"
          :disabled="!feedback.trim()"
          iconLeft="send"
          @click="send"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
/**
 * Feedback on an AI pricer ARV. The backend (crm.api.arv_feedback.send) posts
 * it as a Talk DM from the rep to the pricer's owner (Exe), mirrored to
 * Mattermost. `details` is the AI's math as plain text (comps used,
 * adjustments) so the owner sees what the rep saw.
 */
import { Button, Dialog, ErrorMessage, FormControl, Textarea, call, toast } from 'frappe-ui'
import { DialogDescription } from 'reka-ui'
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  arv: { type: [Number, String], required: true },
  lead: { type: String, default: '' },
  address: { type: String, default: '' },
  details: { type: String, default: '' },
  recipientName: { type: String, default: 'Exe' },
})
const show = defineModel({ type: Boolean, default: false })

const feedback = ref('')
const userArv = ref('')
const error = ref('')
const sending = ref(false)
const feedbackInput = ref(null)

const money = (v) => (Number(v) ? '$' + Math.round(Number(v)).toLocaleString('en-US') : '—')

watch(show, (open) => {
  if (!open) return
  feedback.value = ''
  userArv.value = ''
  error.value = ''
  nextTick(() => feedbackInput.value?.$el?.querySelector('textarea')?.focus())
})

function onKeydown(e) {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    send()
  }
}

async function send() {
  if (sending.value || !feedback.value.trim()) return
  sending.value = true
  error.value = ''
  try {
    const r = await call('crm.api.arv_feedback.send', {
      ai_arv: props.arv,
      feedback: feedback.value,
      lead: props.lead || null,
      address: props.address,
      user_arv: userArv.value || null,
      details: props.details,
    })
    toast.success(__('Sent to {0}', [r?.to || props.recipientName]))
    show.value = false
  } catch (e) {
    error.value = e.messages?.[0] || e.message || __('Could not send the feedback.')
  } finally {
    sending.value = false
  }
}
</script>
