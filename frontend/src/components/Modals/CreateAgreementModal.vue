<template>
  <Dialog v-model="show" :options="{ title: __('Create Agreement') }">
    <template #body-content>
      <!-- Form -->
      <div v-if="!result" class="flex flex-col gap-4 text-base">
        <div v-if="propertyAddress" class="flex flex-col gap-0.5">
          <div class="text-xs text-ink-gray-5">{{ __('Property') }}</div>
          <div class="text-ink-gray-8">{{ propertyAddress }}</div>
        </div>

        <FormControl
          type="select"
          :label="__('Agreement type')"
          v-model="template"
          :options="[
            { label: __('Standard (no novation)'), value: 'standard' },
            { label: __('Novation (+ Attorney-in-Fact page)'), value: 'novation' },
            { label: __('SubTo (buyer takes over the existing mortgage)'), value: 'subto' },
            { label: __('Amendment (price / closing date / other)'), value: 'amendment' },
            { label: __('Cancellation / release of earnest money'), value: 'cancellation' },
            { label: __('Unilateral termination (no earnest money)'), value: 'termination' },
          ]"
        />

        <!-- Seller 1 is the lead; if the lead has no email on file, ask for one
             (optional — Documenso doesn't require it). -->
        <FormControl
          v-if="!isTermination && !leadHasEmail"
          type="email"
          :label="__('Seller 1 email (optional)')"
          v-model="seller1Email"
          :placeholder="__('name@example.com')"
        />

        <FormControl
          v-if="!isTermination"
          type="select"
          :label="__('How many sellers?')"
          v-model="sellerCount"
          :options="[
            { label: __('One seller'), value: '1' },
            { label: __('Two sellers'), value: '2' },
          ]"
        />

        <!-- Seller 1 comes from the lead; only Seller 2 needs collecting. -->
        <div
          v-if="!isTermination && sellerCount === '2'"
          class="flex flex-col gap-3 rounded-md bg-surface-gray-2 px-3 py-3"
        >
          <div class="text-xs text-ink-gray-5">
            {{ __('Second seller (e.g. spouse / co-owner)') }}
          </div>
          <FormControl
            type="text"
            :label="__('Full legal name')"
            v-model="seller2Name"
            :placeholder="__('Mary Q. Seller')"
          />
          <FormControl
            type="email"
            :label="__('Email (optional)')"
            v-model="seller2Email"
            :placeholder="__('name@example.com')"
          />
        </div>

        <div class="text-sm text-ink-gray-5">
          {{
            isTermination
              ? __(
                  'Owner and property details are pre-filled from this lead and stay editable. Only the company representative signs. No email is sent.',
                )
              : __(
                  'Buyer fields are pre-filled from this lead and stay editable. No email is sent — you get a link to review, complete and sign.',
                )
          }}
        </div>

        <ErrorMessage :message="error" />
      </div>

      <!-- Success -->
      <div v-else class="flex flex-col gap-4 text-base">
        <div
          class="flex items-start gap-2.5 rounded-md bg-surface-green-2 px-3 py-2.5 text-ink-green-3"
        >
          <FeatherIcon name="check-circle" class="mt-0.5 size-4 shrink-0" />
          <div class="text-ink-gray-8">
            {{ __('Draft created') }} — {{ result.template }}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-xs text-ink-gray-5">
            {{
              isTermination
                ? __('Company representative link (review & sign)')
                : __('Buyer link (review, edit & sign)')
            }}
          </div>
          <div class="flex items-center gap-2">
            <div
              class="flex-1 truncate rounded-md bg-surface-gray-2 px-2.5 py-2 text-sm text-ink-gray-7"
            >
              {{ result.buyer_link }}
            </div>
            <Button :icon="copied ? 'check' : 'copy'" @click="copy(result.buyer_link)" />
            <Button
              :label="__('Open')"
              variant="solid"
              @click="openLink(result.buyer_link)"
            />
          </div>
        </div>

        <div v-if="result.seller_links?.length" class="flex flex-col gap-1.5">
          <div class="text-xs text-ink-gray-5">
            {{ __('Seller links (send when ready)') }}
          </div>
          <div
            v-for="sl in result.seller_links"
            :key="sl.link"
            class="flex items-center gap-2"
          >
            <div class="w-24 shrink-0 truncate text-sm text-ink-gray-7">
              {{ sl.name }}
            </div>
            <div
              class="flex-1 truncate rounded-md bg-surface-gray-2 px-2.5 py-2 text-xs text-ink-gray-6"
            >
              {{ sl.link }}
            </div>
            <Button icon="copy" @click="copy(sl.link)" />
          </div>
        </div>

        <!-- One tap to grab every link as a labeled block for an email/text. -->
        <Button
          class="w-full"
          :label="__('Copy all links')"
          :icon="allCopied ? 'check' : 'copy'"
          @click="copyAll"
        />
      </div>
    </template>

    <template #actions>
      <Button
        v-if="!result"
        class="w-full"
        variant="solid"
        :label="__('Create Draft')"
        :loading="loading"
        @click="createDraft"
      />
      <Button v-else class="w-full" :label="__('Done')" @click="show = false" />
    </template>
  </Dialog>
</template>

<script setup>
import {
  call,
  Dialog,
  Button,
  FormControl,
  ErrorMessage,
  FeatherIcon,
  toast,
} from 'frappe-ui'
import { ref, computed, watch } from 'vue'

const props = defineProps({
  referenceDoc: { type: Object, default: () => ({}) },
  options: { type: Object, default: () => ({ afterCreate: () => {} }) },
  // Optional CRM Buyer to link the agreement to (set when creating from a
  // buyer page, so it shows on that buyer's Agreements card).
  buyer: { type: String, default: '' },
})

const show = defineModel({ type: Boolean })

const template = ref('standard')
const sellerCount = ref('1')
const seller1Email = ref('')
const seller2Name = ref('')
const seller2Email = ref('')
const loading = ref(false)
const error = ref(null)
const result = ref(null)
const copied = ref(false)
const allCopied = ref(false)

const propertyAddress = computed(() => props.referenceDoc?.property_address || '')
const leadHasEmail = computed(() => !!props.referenceDoc?.email)
const isTermination = computed(() => template.value === 'termination')

// Reset to a clean form each time the modal opens.
watch(show, (open) => {
  if (!open) return
  error.value = null
  result.value = null
  copied.value = false
  allCopied.value = false
  template.value = 'standard'
  sellerCount.value = '1'
  seller1Email.value = ''
  seller2Name.value = ''
  seller2Email.value = ''
})

function copy(text) {
  navigator.clipboard?.writeText(text)
  copied.value = true
  toast.success(__('Link copied'))
}

function openLink(url) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

// A labeled, paste-ready block of every link for an email/text.
function copyAll() {
  const r = result.value
  if (!r) return
  const primaryRole = isTermination.value ? 'Company representative' : 'Buyer'
  const lines = [`${primaryRole} (review & sign): ${r.buyer_link}`]
  for (const sl of r.seller_links || []) {
    lines.push(`${sl.name} (sign): ${sl.link}`)
  }
  navigator.clipboard?.writeText(lines.join('\n'))
  allCopied.value = true
  toast.success(__('All links copied'))
}

async function createDraft() {
  const lead = props.referenceDoc?.name
  if (!lead || loading.value) return
  if (!isTermination.value && sellerCount.value === '2' && !seller2Name.value) {
    error.value = __('Enter the second seller’s name.')
    return
  }
  loading.value = true
  error.value = null
  try {
    result.value = await call('crm.api.agreement.create_docuseal_agreement', {
      lead,
      template: template.value,
      two_sellers: sellerCount.value,
      seller1_email: seller1Email.value,
      seller2_name: seller2Name.value,
      seller2_email: seller2Email.value,
      buyer: props.buyer || undefined,
    })
    toast.success(__('Agreement draft created'))
    props.options.afterCreate?.()
  } catch (e) {
    error.value = e.messages?.[0] || e.message || __('Failed to create draft')
  } finally {
    loading.value = false
  }
}
</script>
