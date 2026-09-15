<template>
  <Dialog v-model="show" :options="{ title: isDone ? __('How did it go?') : __('Why skip this one?') }">
    <template #body-content>
      <DialogDescription class="sr-only">
        {{ isDone ? __('Choose what happened on this call.') : __('Choose why this card is being skipped.') }}
      </DialogDescription>
      <div class="flex flex-col gap-4">
        <div
          v-if="item?.lead_name"
          class="rounded-lg bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-7"
        >
          {{ item.lead_name }}
        </div>

        <!-- One tap picks the outcome. Short answers, one per row, so the whole
             list can be read without parsing it. Same picker for Done and Skip. -->
        <div class="flex flex-col gap-2">
          <button
            v-for="option in options"
            :key="option"
            class="flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-base"
            :class="
              outcome === option
                ? 'border-outline-gray-4 bg-surface-gray-2 font-medium text-ink-gray-9'
                : 'border-outline-gray-1 bg-surface-white text-ink-gray-7 hover:border-outline-gray-3 hover:bg-surface-gray-1'
            "
            @click="choose(option)"
          >
            <FeatherIcon
              :name="outcome === option ? 'check-circle' : 'circle'"
              class="size-4 shrink-0"
              :class="outcome === option ? 'text-ink-green-3' : 'text-ink-gray-4'"
            />
            {{ __(option) }}
          </button>
        </div>

        <!-- The note is required for "Other" (which means nothing on its own). -->
        <div v-if="showNote">
          <div class="mb-1.5 text-xs text-ink-gray-5">{{ noteLabel }}</div>
          <Textarea
            ref="noteInput"
            v-model="note"
            :rows="3"
            :placeholder="notePlaceholder"
            @keydown.enter.stop="onNoteEnter"
          />
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex w-full items-center gap-2">
        <Button :label="__('Cancel')" :disabled="saving" @click="show = false" />
        <Button
          class="ml-auto"
          variant="solid"
          :theme="isDone ? 'green' : 'gray'"
          :label="isDone ? __('Mark done') : __('Skip card')"
          :loading="saving"
          @click="confirm"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { Button, Dialog, ErrorMessage, FeatherIcon, Textarea } from 'frappe-ui'
import { DialogDescription } from 'reka-ui'
import { computed, nextTick, ref, watch } from 'vue'

// Kept in step with `DONE_OUTCOMES` / `SKIP_OUTCOMES` in crm/api/today_board.py,
// which validates the value on the way in.
const DONE_OUTCOMES = [
  'Connected',
  'No Answer',
  'Left a Voicemail',
  'Sent a Text',
  'Booked an Appointment',
  'Other',
]
const SKIP_OUTCOMES = [
  'Dead lead',
  'Lost',
  'Already scheduled',
  'Already contacted',
  'Check with Dennis',
  'Follow up later',
  'Not selling',
  "I didn't have time today",
  'Other',
]

const props = defineProps({
  item: { type: Object, default: null },
  state: { type: String, default: 'Done' },
  saving: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm'])
const show = defineModel({ type: Boolean })

const outcome = ref('')
const note = ref('')
const error = ref('')
const noteInput = ref(null)

const isDone = computed(() => props.state === 'Done')
const options = computed(() => (isDone.value ? DONE_OUTCOMES : SKIP_OUTCOMES))
const showNote = computed(() => outcome.value === 'Other')
const noteLabel = computed(() =>
  isDone.value ? __('What happened?') : __("What's the reason?"),
)
const notePlaceholder = computed(() =>
  isDone.value
    ? __('Tell us a little more…')
    : __('Wrong number, bad time, under contract…'),
)

// Reset every time the modal opens: it is reused for every card on the board,
// and inheriting the previous card's answer is how a wrong outcome gets saved
// without anyone touching it.
watch(show, (open) => {
  if (!open) return
  outcome.value = ''
  note.value = ''
  error.value = ''
})

function choose(option) {
  outcome.value = option
  error.value = ''
  if (option === 'Other') focusNote()
}

function focusNote() {
  nextTick(() => noteInput.value?.$el?.querySelector('textarea')?.focus())
}

function onNoteEnter(event) {
  // Cmd/Ctrl-Enter saves; a bare Enter keeps writing, because these answers are
  // sentences.
  if (event.metaKey || event.ctrlKey) {
    event.preventDefault()
    confirm()
  }
}

function confirm() {
  if (props.saving) return
  const text = note.value.trim()
  if (!outcome.value) {
    error.value = isDone.value
      ? __('Pick what happened on the call.')
      : __('Pick a reason.')
    return
  }
  if (outcome.value === 'Other' && !text) {
    error.value = isDone.value
      ? __('Say a little more about what happened.')
      : __('Say why this one is being skipped.')
    return
  }
  emit('confirm', { outcome: outcome.value, outcome_note: text })
}
</script>
