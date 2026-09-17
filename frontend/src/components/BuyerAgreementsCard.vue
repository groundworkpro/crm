<template>
  <div class="border-t px-5 py-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
        <FeatherIcon name="file-text" class="size-4 text-ink-gray-7" />
        {{ __('Agreements') }}
      </div>
      <!-- Agreements are keyed to a property (lead), so creating one picks
           which engaged deal it's for. -->
      <Dropdown v-if="deals.length" :options="createOptions">
        <Button :tooltip="__('Create purchase agreement')" icon="plus" variant="ghost" />
      </Dropdown>
      <Button
        v-else
        :tooltip="__('Create purchase agreement')"
        icon="plus"
        variant="ghost"
        @click="noDeals"
      />
    </div>

    <div v-if="agreements.length" class="mt-2.5 flex flex-col gap-3">
      <div
        v-for="a in agreements"
        :key="a.name"
        class="flex flex-col gap-1.5 rounded-md bg-surface-gray-2 px-3 py-2.5 text-sm"
      >
        <div class="flex items-center justify-between gap-2">
          <div class="truncate text-ink-gray-8">{{ a.template_title }}</div>
          <Badge
            class="shrink-0"
            :theme="statusTheme(a)"
            variant="subtle"
            :label="statusLabel(a)"
          />
        </div>

        <router-link
          :to="{ name: 'Lead', params: { leadId: a.lead } }"
          class="truncate text-xs text-ink-gray-6 hover:text-ink-gray-9 hover:underline"
        >
          {{ a.property_label }}
        </router-link>

        <div class="flex items-center gap-1.5 text-xs text-ink-gray-6">
          <span>{{ a.signed_count || 0 }}/{{ a.total_signers || 0 }} {{ __('signed') }}</span>
          <span v-if="a.last_event" class="text-ink-gray-4">·</span>
          <span v-if="a.last_event" class="truncate">{{ eventLabel(a.last_event) }}</span>
        </div>

        <div v-if="a.is_signed" class="mt-0.5 flex items-center gap-2">
          <a
            :href="signedUrl(a)"
            target="_blank"
            rel="noopener"
            class="block min-w-0 flex-1"
          >
            <Button class="w-full" size="sm" theme="green" :label="__('Open signed PDF')">
              <template #prefix>
                <FeatherIcon name="external-link" class="size-3.5" />
              </template>
            </Button>
          </a>
          <Button
            size="sm"
            icon="copy"
            :tooltip="__('Copy public PDF link')"
            @click="copy(signedUrl(a))"
          />
        </div>

        <div class="mt-0.5 flex items-center gap-2">
          <Button
            class="flex-1"
            size="sm"
            :label="__('Open buyer link')"
            @click="openLink(a.buyer_link)"
          />
          <Button size="sm" icon="copy" @click="copy(a.buyer_link)" />
        </div>

        <div class="text-xs text-ink-gray-4">
          {{ a.created_by_name || a.owner }} · {{ formatDate(a.creation, '', true) }}
        </div>
      </div>
    </div>

    <div v-else class="mt-2 text-sm text-ink-gray-5">
      {{ __('No agreements yet.') }}
    </div>
  </div>
</template>

<script setup>
import { formatDate } from '@/utils'
import { globalStore } from '@/stores/global'
import {
  Button,
  Badge,
  Dropdown,
  FeatherIcon,
  createResource,
  toast,
} from 'frappe-ui'
import { computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  buyer: { type: String, required: true },
  // engaged properties: [{ lead, label }] — drives the create picker + realtime match
  deals: { type: Array, default: () => [] },
})
const emit = defineEmits(['create'])

const { $socket } = globalStore()

const agreementsResource = createResource({
  url: 'crm.api.agreement.get_buyer_agreements',
  cache: ['buyer_agreements', props.buyer],
  params: { buyer: props.buyer },
  auto: true,
})

const agreements = computed(() => agreementsResource.data || [])

const createOptions = computed(() =>
  props.deals.map((d) => ({
    label: d.label,
    onClick: () => emit('create', d.lead),
  })),
)

function noDeals() {
  toast.error(__('Add this buyer to a deal first — agreements belong to a property.'))
}

// Document status → a colored badge (Documenso + DocuSeal statuses).
function statusLabel(a) {
  const s = (a.agreement_status || '').toUpperCase()
  if (s === 'COMPLETED') return __('Completed')
  if (s === 'REJECTED' || s === 'DECLINED') return __('Declined')
  if (s === 'CANCELLED') return __('Cancelled')
  if (s === 'EXPIRED') return __('Expired')
  if (a.signed_count && a.total_signers && a.signed_count >= a.total_signers)
    return __('Completed')
  if (a.signed_count > 0) return __('Signing')
  return __('Awaiting')
}
function statusTheme(a) {
  const s = (a.agreement_status || '').toUpperCase()
  if (s === 'COMPLETED') return 'green'
  if (['REJECTED', 'CANCELLED', 'DECLINED', 'EXPIRED'].includes(s)) return 'red'
  if (a.signed_count > 0) return 'blue'
  return 'orange'
}
// Documenso `DOCUMENT_SIGNED` or DocuSeal `form.completed` → "Signed".
function eventLabel(ev) {
  return String(ev)
    .replace(/^DOCUMENT_/, '')
    .replace(/^(form|submission)\./, '')
    .replace(/[_.]/g, ' ')
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase())
}

function copy(text) {
  if (!text) return
  navigator.clipboard?.writeText(text)
  toast.success(__('Link copied'))
}
function openLink(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer')
}

function signedUrl(a) {
  return (
    a.signed_pdf_url ||
    `/api/method/crm.api.agreement.download_signed_agreement?agreement=${encodeURIComponent(a.name)}`
  )
}

// crm_esign events are lead-scoped — reload when they hit an engaged property.
function onEsign(data) {
  const mine =
    props.deals.some((d) => d.lead === data.reference_docname) ||
    agreements.value.some((a) => a.lead === data.reference_docname)
  if (data.reference_doctype === 'CRM Lead' && mine) {
    agreementsResource.reload()
  }
}
onMounted(() => $socket.on('crm_esign', onEsign))
onBeforeUnmount(() => $socket.off('crm_esign', onEsign))

defineExpose({ reload: () => agreementsResource.reload() })
</script>
