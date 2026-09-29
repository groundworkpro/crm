<template>
  <div
    class="flex flex-col overflow-hidden rounded-md bg-surface-cards text-base shadow"
  >
    <div class="truncate px-3 py-2 font-medium text-ink-gray-9">
      {{ subject }}
    </div>
    <div
      v-for="(msg, i) in ordered"
      :key="msg.name || i"
      class="border-t border-outline-gray-modals"
    >
      <button
        type="button"
        class="w-full px-3 text-left"
        :class="isOpen(i) ? 'pt-3' : 'py-2 hover:bg-surface-gray-1'"
        @click="toggle(i)"
      >
        <div class="flex items-baseline justify-between gap-2">
          <div class="min-w-0 truncate text-ink-gray-9">
            <span class="font-medium">{{
              msg.data?.sender_full_name || senderName(msg)
            }}</span>
            <span v-if="isOpen(i)" class="ml-1 text-sm text-ink-gray-5">
              &lt;{{ msg.data?.sender }}&gt;
            </span>
            <span v-else class="ml-2 text-sm text-ink-gray-5">
              {{ preview(msg) }}
            </span>
          </div>
          <div class="shrink-0 text-xs text-ink-gray-5">
            {{ formatDate(msg.communication_date || msg.creation, 'MMM D, h:mm a') }}
          </div>
        </div>
      </button>
      <div v-if="isOpen(i)" class="px-3 pb-3">
        <div class="text-sm text-ink-gray-5">
          {{ __('To') }}: {{ msg.data?.recipients }}
        </div>
        <EmailContent :content="msg.data?.content || ''" />
        <!-- attachments: pictures as thumbnails that can go to the lead's
             Photos folder, everything else as a file chip -->
        <div v-if="msg.data?.attachments?.length" class="mt-2 flex flex-col gap-2">
          <div v-if="pictures(msg).length" class="flex flex-wrap gap-1.5">
            <div
              v-for="a in pictures(msg)"
              :key="a.name"
              class="group relative size-24 overflow-hidden rounded-md bg-surface-gray-2"
            >
              <a :href="a.file_url" target="_blank" rel="noopener noreferrer">
                <img
                  :src="a.file_url"
                  :alt="a.file_name"
                  loading="lazy"
                  class="size-full object-cover"
                />
              </a>
              <span
                v-if="saves.enabled && saves.isSaved({ file: a.name })"
                class="pointer-events-none absolute bottom-1 right-1 rounded-full bg-black/60 px-1.5 text-2xs text-white"
              >
                ✓ {{ __('Saved') }}
              </span>
            </div>
          </div>
          <div v-if="otherFiles(msg).length" class="flex flex-wrap gap-2">
            <AttachmentItem
              v-for="a in otherFiles(msg)"
              :key="a.name"
              :label="a.file_name"
              :url="a.file_url"
            />
          </div>
          <div v-if="saves.enabled && pictures(msg).length">
            <template v-for="st in [emailRunState(msg)]" :key="msg.name">
              <span
                v-if="st.saved === st.total"
                class="inline-flex items-center gap-1 rounded-md bg-surface-green-2 px-2.5 py-1 text-xs font-medium text-ink-green-3"
              >
                ✓
                {{
                  st.total === 1
                    ? __('Saved to Photos')
                    : __('{0} photos saved to Photos', [st.total])
                }}
              </span>
              <span v-else-if="st.pending" class="text-xs text-ink-gray-5">
                {{ __('Saving {0} of {1}…', [st.saved + 1, st.total]) }}
              </span>
              <Button
                v-else
                size="sm"
                variant="solid"
                :label="
                  st.total === 1
                    ? __('Save photo to Photos')
                    : __('Save all {0} photos to Photos', [st.total - st.saved])
                "
                @click="saves.save(pictures(msg).map((a) => ({ file: a.name })))"
              />
            </template>
          </div>
        </div>
        <div class="mt-2 flex justify-end">
          <Button
            variant="ghost"
            :icon="ReplyIcon"
            :label="__('Reply')"
            @click="reply(msg)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import EmailContent from '@/components/Activities/EmailContent.vue'
import AttachmentItem from '@/components/AttachmentItem.vue'
import { useLeadPhotoSaves } from '@/composables/leadPhotoSaves'
import ReplyIcon from '@/components/Icons/ReplyIcon.vue'
import { formatDate } from '@/utils'
import { Button } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  // CRM Lead name; enables "Save to Photos" on pictures attached to emails
  lead: { type: String, default: '' },
})
const emit = defineEmits(['reply'])

const saves = useLeadPhotoSaves(props.lead)

const isPicture = (a) =>
  /\.(jpe?g|png|gif|webp|heic|heif)$/i.test(a.file_name || a.file_url || '')
const pictures = (msg) => (msg.data?.attachments || []).filter(isPicture)
const otherFiles = (msg) =>
  (msg.data?.attachments || []).filter((a) => !isPicture(a))

function emailRunState(msg) {
  const items = pictures(msg).map((a) => ({ file: a.name }))
  return {
    total: items.length,
    saved: items.filter((i) => saves.isSaved(i)).length,
    pending: items.some((i) => saves.isPending(i)),
  }
}

const ordered = computed(() =>
  [...props.messages].sort(
    (a, b) =>
      new Date(b.communication_date || b.creation) -
      new Date(a.communication_date || a.creation),
  ),
)

const open = ref(new Set())

watch(
  ordered,
  (list) => {
    open.value = new Set(list.length ? [0] : [])
  },
  { immediate: true },
)

function isOpen(i) {
  return open.value.has(i)
}

function toggle(i) {
  const next = new Set(open.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  open.value = next
}

function reply(msg) {
  emit('reply', msg.data)
}

function senderName(msg) {
  const from = msg.data?.sender || ''
  return from.split('@')[0] || from
}

function preview(msg) {
  const html = msg.data?.content || ''
  const text = html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
  return text.slice(0, 80)
}

const subject = computed(() => {
  const first = ordered.value[0]
  return (first?.data?.subject || '').replace(/^(re:\s*)+/i, '')
})
</script>
