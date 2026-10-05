<template>
  <div :id="activity.name" class="group">
    <div class="mb-1 flex items-center justify-stretch gap-2 py-1 text-xs">
      <div class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5">
        <span class="font-medium text-ink-gray-8">
          {{ activity.owner_name }}
        </span>
        <span>{{ __('commented') }}</span>
      </div>
      <div class="ml-auto flex items-center gap-1 whitespace-nowrap">
        <Button
          v-if="canEdit && !editing"
          class="opacity-0 transition-opacity group-hover:opacity-100"
          variant="ghost"
          :tooltip="__('Edit')"
          @click="startEdit"
        >
          <template #icon>
            <EditIcon class="h-3.5 w-3.5 text-ink-gray-5" />
          </template>
        </Button>
        <Button
          v-if="canEdit && !editing"
          class="opacity-0 transition-opacity group-hover:opacity-100"
          variant="ghost"
          :tooltip="__('Delete')"
          @click="showDeleteConfirm = true"
        >
          <template #icon>
            <LucideTrash2 class="h-3.5 w-3.5 text-ink-gray-5" />
          </template>
        </Button>
        <Tooltip :text="formatDate(activity.creation)">
          <div class="text-xs text-ink-gray-5">
            {{ formatDate(activity.creation, 'h:mm a') }}
          </div>
        </Tooltip>
      </div>
    </div>
    <div
      v-if="editing"
      class="rounded bg-surface-gray-1 px-3 py-[7.5px] text-sm leading-5"
    >
      <TextEditor
        ref="editor"
        editor-class="prose-f text-p-sm max-w-none focus:outline-none"
        :content="editedContent"
        :placeholder="__('Edit comment...')"
        :editable="true"
        :bubbleMenu="true"
        :mentions="users"
        @change="editedContent = $event"
      />
      <div class="mt-2 flex justify-end gap-2">
        <Button
          :label="__('Cancel')"
          :disabled="saving"
          @click="cancelEdit"
        />
        <Button
          variant="solid"
          :label="__('Save')"
          :loading="saving"
          @click="saveEdit"
        />
      </div>
    </div>
    <div
      v-else
      class="cursor-pointer rounded bg-surface-gray-1 px-3 py-[7.5px] text-sm leading-5 transition-all duration-300 ease-in-out"
    >
      <CashOfferComment
        v-if="isCashOffer"
        :html="activity.content"
        :lead="docname"
        @saved="activities?.reload()"
      />
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-else class="prose-f text-p-sm" v-html="sanitizeHTML(activity.content)" />
      <div v-if="activity.attachments.length" class="mt-2 flex flex-wrap gap-2">
        <AttachmentItem
          v-for="a in activity.attachments"
          :key="a.file_url"
          :label="a.file_name"
          :url="a.file_url"
        />
      </div>
    </div>
    <Dialog
      v-model="showDeleteConfirm"
      :options="{
        title: __('Delete comment'),
        message: __('Are you sure you want to delete this comment? This cannot be undone.'),
        actions: [
          {
            label: __('Delete'),
            variant: 'solid',
            theme: 'red',
            loading: deleting,
            onClick: deleteComment,
          },
        ],
      }"
    />
  </div>
</template>
<script setup>
import AttachmentItem from '@/components/AttachmentItem.vue'
import CashOfferComment from '@/components/Activities/CashOfferComment.vue'
import EditIcon from '@/components/Icons/EditIcon.vue'
import LucideTrash2 from '~icons/lucide/trash-2'
import { sessionStore } from '@/stores/session'
import { usersStore } from '@/stores/users'
import { formatDate, sanitizeHTML } from '@/utils'
import { Button, Tooltip, TextEditor, Dialog, call, toast } from 'frappe-ui'
import { computed, ref } from 'vue'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
  docname: { type: String, default: '' },
})

// Pass the activities resource (v-model) so we can refresh after an edit.
const activities = defineModel({ type: Object })

const { user } = sessionStore()
const { users: usersList } = usersStore()

const canEdit = computed(() => props.activity.owner === user)

const isCashOffer = computed(() => {
  const html = props.activity.content || ''
  return (
    /data-cash-offer=/.test(html) ||
    /<b>(Cash offer|Novation offer|List it|Rental calc|Rental MAO)<\/b>/i.test(html)
  )
})

const editing = ref(false)
const saving = ref(false)
const editedContent = ref('')

const showDeleteConfirm = ref(false)
const deleting = ref(false)

function startEdit() {
  editedContent.value = props.activity.content
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

async function saveEdit() {
  if (!editedContent.value?.trim()) return
  saving.value = true
  try {
    await call('crm.api.comment.edit_comment', {
      comment_name: props.activity.name,
      content: editedContent.value,
    })
    editing.value = false
    activities.value?.reload()
    toast.success(__('Comment updated'))
  } catch (e) {
    toast.error(e?.messages?.[0] || __('Failed to update comment'))
  } finally {
    saving.value = false
  }
}

async function deleteComment() {
  deleting.value = true
  try {
    await call('crm.api.comment.delete_comment', {
      comment_name: props.activity.name,
    })
    showDeleteConfirm.value = false
    activities.value?.reload()
    toast.success(__('Comment deleted'))
  } catch (e) {
    toast.error(e?.messages?.[0] || __('Failed to delete comment'))
  } finally {
    deleting.value = false
  }
}

const users = computed(() => {
  return (
    usersList.data?.crmUsers
      ?.filter((u) => u.enabled)
      .map((u) => ({
        label: u.full_name.trimEnd(),
        value: u.name,
      })) || []
  )
})
</script>
