<template>
  <div class="border-t px-5 py-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
        <FeatherIcon name="file-text" class="size-4 text-ink-gray-7" />
        {{ __('Agreements') }}
      </div>
      <Button
        :tooltip="__('Create agreement')"
        icon="plus"
        variant="ghost"
        @click="emit('create')"
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
          <div class="flex shrink-0 items-center gap-1">
            <Badge :theme="statusTheme(a)" variant="subtle" :label="statusLabel(a)" />
            <Button
              variant="ghost"
              size="sm"
              icon="archive"
              :tooltip="__('Archive agreement')"
              @click="confirmArchive(a)"
            />
          </div>
        </div>

        <div class="flex items-center gap-1.5 text-xs text-ink-gray-6">
          <span>{{ a.signed_count || 0 }}/{{ a.total_signers || 0 }} {{ __('signed') }}</span>
          <span v-if="a.last_event" class="text-ink-gray-4">·</span>
          <span v-if="a.last_event" class="truncate">{{ eventLabel(a.last_event) }}</span>
        </div>

        <!-- Built by hand in DocuSeal and matched back to this lead by the
             webhook. Surfaced (with the matching evidence on hover) so an
             auto-link is never mistaken for something the CRM itself sent. -->
        <div
          v-if="a.source === 'adopted'"
          class="flex items-center gap-1 text-xs text-ink-amber-3"
          :title="a.match_basis ? __('Matched by') + ': ' + a.match_basis : ''"
        >
          <FeatherIcon name="link" class="size-3" />
          <span class="truncate">{{ __('Auto-linked from DocuSeal') }}</span>
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
            :label="
              isTerminationAgreement(a)
                ? __('Open company representative link')
                : __('Open buyer link')
            "
            @click="openLink(a.buyer_link)"
          />
          <Button size="sm" icon="copy" @click="copy(a.buyer_link)" />
        </div>

        <div v-if="a.seller_links?.length" class="flex flex-col gap-1">
          <div
            v-for="sl in a.seller_links"
            :key="sl.link"
            class="flex items-center justify-between gap-2 text-xs text-ink-gray-6"
          >
            <span class="truncate">{{ sl.name }}</span>
            <div class="flex shrink-0 items-center gap-2">
              <button class="text-ink-gray-5 hover:text-ink-gray-8" @click="openLink(sl.link)">
                {{ __('open') }}
              </button>
              <button class="text-ink-gray-5 hover:text-ink-gray-8" @click="copy(sl.link)">
                {{ __('copy') }}
              </button>
            </div>
          </div>
        </div>

        <button
          class="self-start text-xs font-medium text-ink-gray-6 hover:text-ink-gray-9"
          @click="copyAll(a)"
        >
          {{ __('Copy all links') }}
        </button>

        <div class="text-xs text-ink-gray-4">
          {{ a.created_by_name || a.owner }} · {{ formatDate(a.creation, '', true) }}
        </div>
      </div>
    </div>

    <div v-else class="mt-2 text-sm text-ink-gray-5">
      {{ __('No agreements yet.') }}
    </div>

    <Dialog
      v-model="showArchive"
      :options="{
        title: __('Archive agreement?'),
        message: __(
          'This hides the agreement from this lead and archives it in DocuSeal. The signing links will stop working. The record is kept, so it can be restored if this was a mistake.',
        ),
        actions: [
          {
            label: __('Archive'),
            variant: 'solid',
            theme: 'red',
            loading: archiving,
            onClick: doArchive,
          },
        ],
      }"
    />
  </div>
</template>

<script setup>
import { formatDate } from '@/utils'
import { globalStore } from '@/stores/global'
import {
  Button,
  Badge,
  Dialog,
  FeatherIcon,
  createResource,
  call,
  toast,
} from 'frappe-ui'
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  lead: { type: String, required: true },
})
const emit = defineEmits(['create'])

const { $socket } = globalStore()

const agreementsResource = createResource({
  url: 'crm.api.agreement.get_agreements',
  cache: ['agreements', props.lead],
  params: { lead: props.lead },
  auto: true,
})

const agreements = computed(() => agreementsResource.data || [])

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

// Public HMAC URL — opens without a CRM session, so the tab's address is a
// link you can send. Session proxy is the fallback if the row predates it.
function signedUrl(a) {
  return (
    a.signed_pdf_url ||
    `/api/method/crm.api.agreement.download_signed_agreement?agreement=${encodeURIComponent(a.name)}`
  )
}

// A labeled, paste-ready block of every link for an email/text.
function isTerminationAgreement(a) {
  return String(a?.template_title || '').startsWith('Unilateral Termination - No EMD')
}

function copyAll(a) {
  const primaryRole = isTerminationAgreement(a) ? 'Company representative' : 'Buyer'
  const lines = [`${primaryRole} (review & sign): ${a.buyer_link}`]
  for (const sl of a.seller_links || []) {
    lines.push(`${sl.name} (sign): ${sl.link}`)
  }
  navigator.clipboard?.writeText(lines.join('\n'))
  toast.success(__('All links copied'))
}

// Archive an agreement (archives the DocuSeal submission + removes it here).
const showArchive = ref(false)
const archiving = ref(false)
const archiveTarget = ref(null)

function confirmArchive(a) {
  archiveTarget.value = a
  showArchive.value = true
}
async function doArchive(close) {
  if (!archiveTarget.value || archiving.value) return
  archiving.value = true
  try {
    await call('crm.api.agreement.archive_agreement', {
      agreement: archiveTarget.value.name,
    })
    toast.success(__('Agreement archived'))
    agreementsResource.reload()
    close?.()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not archive agreement'))
  } finally {
    archiving.value = false
  }
}

function onEsign(data) {
  if (
    data.reference_doctype === 'CRM Lead' &&
    data.reference_docname === props.lead
  ) {
    agreementsResource.reload()
  }
}
onMounted(() => $socket.on('crm_esign', onEsign))
onBeforeUnmount(() => $socket.off('crm_esign', onEsign))
</script>
