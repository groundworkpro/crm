<template>
  <div class="pb-3">
    <template v-for="(sms, i) in messages" :key="sms.name">
    <!-- day divider: the bubbles only show a time, so without this a thread
         spanning several days reads as out of order -->
    <div
      v-if="startsDay(i)"
      class="sms-day-divider flex items-center gap-3 text-xs text-ink-gray-5"
      :class="i == 0 ? 'mb-3' : 'mb-3 mt-5'"
    >
      <div class="flex-1 border-t border-outline-gray-2" />
      <span>{{ dayLabel(sms.creation) }}</span>
      <div class="flex-1 border-t border-outline-gray-2" />
    </div>
    <div
      class="activity group flex gap-2"
      :class="[
        sms.type == 'Outgoing' ? 'flex-row-reverse' : '',
        startsRun(i) ? 'mt-3' : 'mt-0.5',
        startsDay(i) ? '!mt-0' : '',
      ]"
    >
      <!-- who sent it: lead avatar on the left, teammate avatar on the right,
           shown once at the end of each run of consecutive messages -->
      <div class="w-6 shrink-0 self-end">
        <template v-if="endsRun(i)">
          <UserAvatar
            v-if="sms.type == 'Outgoing' && sms.sender"
            :user="sms.sender"
            size="md"
          />
          <Avatar
            v-else-if="sms.type != 'Outgoing'"
            :image="contactImage"
            :label="contactName || __('Lead')"
            size="md"
          />
        </template>
      </div>
      <div
        class="flex max-w-[85%] flex-col"
        :class="sms.type == 'Outgoing' ? 'items-end' : 'items-start'"
      >
        <div
          v-if="startsRun(i) && senderLabel(sms)"
          class="mb-0.5 px-1 text-xs text-ink-gray-5"
        >
          {{ senderLabel(sms) }}
        </div>
        <div
          :id="sms.name"
          class="group/message relative rounded-lg p-1.5 pl-2 text-base shadow-sm whitespace-pre-wrap"
          :class="
            sms.status == 'scheduled'
              ? 'border border-dashed border-blue-400 bg-surface-blue-1 text-ink-gray-9'
              : sms.type == 'Outgoing'
                ? 'bg-blue-500 text-white'
                : 'bg-surface-gray-2 text-ink-gray-9'
          "
        >
          <Badge
            v-if="sms.status == 'failed' || sms.status == 'undelivered'"
            theme="red"
            :label="sms.status"
            class="absolute -top-2 right-0"
          />
          <Badge
            v-else-if="sms.status == 'canceled'"
            theme="gray"
            :label="__('Not sent — lead closed')"
            class="absolute -top-2 right-0"
          />
          <!-- a scheduled placeholder: says when, and can be pulled back -->
          <div
            v-if="sms.status == 'scheduled'"
            class="mb-1 flex items-center gap-1.5 text-xs text-ink-blue-3"
          >
            <FeatherIcon name="clock" class="size-3" />
            <span>
              {{ __('Scheduled') }} · {{ formatDate(sms.creation, 'ddd, MMM D h:mm a') }}
            </span>
            <button
              type="button"
              class="ml-1 underline decoration-dotted hover:text-ink-red-4"
              :disabled="canceling === sms.name"
              @click="cancelScheduled(sms)"
            >
              {{ __('Cancel') }}
            </button>
          </div>
          <SMSMedia
            v-if="sms.media?.length"
            :media="sms.media"
            :class="sms.message ? 'mb-1' : ''"
          />
          <div class="flex gap-2 justify-between">
            <div v-if="sms.message" class="break-words">{{ sms.message }}</div>
            <div
              class="-mb-1 flex shrink-0 items-end gap-1"
              :class="
                sms.type == 'Outgoing' && sms.status != 'scheduled'
                  ? 'text-white'
                  : 'text-ink-gray-5'
              "
            >
              <!-- Texted from Knock: name it, and open the conversation there. -->
              <a
                v-if="sms.knock_url"
                :href="sms.knock_url"
                target="_blank"
                rel="noopener"
                class="text-2xs underline underline-offset-2 hover:opacity-80"
                :title="__('Sent through Knock. Open this conversation in Knock')"
              >Knock ↗</a>
              <Tooltip :text="formatDate(sms.creation, 'ddd, MMM D, YYYY')">
                <div class="text-2xs">
                  {{ formatDate(sms.creation, 'h:mm a') }}
                </div>
              </Tooltip>
              <div v-if="sms.type == 'Outgoing' && sms.status != 'scheduled'">
                <CheckIcon
                  v-if="['sent', 'queued', 'success'].includes(sms.status)"
                  class="size-4"
                />
                <DoubleCheckIcon
                  v-else-if="['delivered', 'read'].includes(sms.status)"
                  class="size-4"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import CheckIcon from '@/components/Icons/CheckIcon.vue'
import DoubleCheckIcon from '@/components/Icons/DoubleCheckIcon.vue'
import SMSMedia from '@/components/Activities/SMSMedia.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { formatDate } from '@/utils'
import { Tooltip, Badge, Avatar, FeatherIcon, call, toast, dayjsLocal } from 'frappe-ui'
import { ref } from 'vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  contactName: { type: String, default: '' },
  contactImage: { type: String, default: '' },
})
const emit = defineEmits(['reload'])

const canceling = ref('')

// Pull back a scheduled text. The server publishes `quo_message` on delete so
// every open thread refreshes; the emit covers a host without that listener.
async function cancelScheduled(sms) {
  canceling.value = sms.name
  try {
    await call('crm.api.scheduled_text.cancel_scheduled_text', { name: sms.name })
    toast.success(__('Scheduled text canceled'))
    emit('reload')
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not cancel'))
  } finally {
    canceling.value = ''
  }
}

// a "run" = consecutive messages from the same person (same direction + same
// sending teammate); the name shows above a run, the avatar at its end
function sameAuthor(a, b) {
  return (
    a && b && a.type == b.type && a.sender == b.sender && sameDay(a, b)
  )
}

function dayKey(sms) {
  return sms ? dayjsLocal(sms.creation).format('YYYY-MM-DD') : ''
}

function sameDay(a, b) {
  return dayKey(a) == dayKey(b)
}

function startsDay(i) {
  return !sameDay(props.messages[i - 1], props.messages[i])
}

function dayLabel(date) {
  const d = dayjsLocal(date)
  const today = dayjsLocal()
  if (d.isSame(today, 'day')) return __('Today')
  if (d.isSame(today.subtract(1, 'day'), 'day')) return __('Yesterday')
  if (d.isSame(today.add(1, 'day'), 'day')) return __('Tomorrow')
  return d.format(d.isSame(today, 'year') ? 'ddd, MMM D' : 'ddd, MMM D, YYYY')
}

function startsRun(i) {
  return !sameAuthor(props.messages[i - 1], props.messages[i])
}

function endsRun(i) {
  return !sameAuthor(props.messages[i], props.messages[i + 1])
}

function senderLabel(sms) {
  if (sms.type == 'Outgoing') return sms.sender_name
  return props.contactName
}
</script>
