<template>
  <div v-if="supported" class="border-t px-5 py-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
        <FeatherIcon name="home" class="size-4 text-ink-gray-7" />
        {{ __('Properties') }}
        <span v-if="rows.length" class="text-sm font-normal text-ink-gray-5">
          {{ rows.length }}
        </span>
      </div>
      <Button
        :tooltip="__('Comp another house for this lead')"
        icon="plus"
        variant="ghost"
        @click="addOpen = true"
      />
    </div>

    <div v-if="rows.length" class="mt-2 flex flex-col gap-1">
      <router-link
        v-for="p in rows"
        :key="p.name"
        :to="{ name: 'Property', params: { propertyId: p.name } }"
        class="flex items-center justify-between gap-2 rounded px-1 py-1 text-sm hover:bg-surface-gray-1"
      >
        <span class="min-w-0 truncate text-ink-gray-8">{{ p.property_address }}</span>
        <span class="flex shrink-0 items-center gap-1.5">
          <span class="text-xs text-ink-gray-5">{{ __(p.status) }}</span>
          <!-- This house's own photo folder, without leaving the lead. -->
          <button
            class="rounded p-0.5 text-ink-gray-5 hover:bg-surface-gray-3 hover:text-ink-gray-8"
            :title="__('Photos of this house')"
            @click.prevent.stop="photosFor = p"
          >
            <FeatherIcon name="camera" class="size-3.5" />
          </button>
        </span>
      </router-link>
    </div>
    <div v-else class="mt-2 text-sm text-ink-gray-5">
      {{ __('No properties linked. A neighbour or a second house can hang off this lead without becoming one.') }}
    </div>

    <div class="mt-2">
      <Autocomplete
        :key="linkKey"
        :options="propOptions"
        :placeholder="__('Link an existing property…')"
        @update:query="onPropQuery"
        @update:modelValue="linkExisting"
      />
    </div>
  </div>

  <PhotoGalleryModal
    v-if="photosFor"
    :model-value="true"
    :lead="photosFor.name"
    :address="photosFor.property_address"
    @update:model-value="(v) => !v && (photosFor = null)"
  />

  <Dialog v-model="addOpen" :options="{ title: __('Add property for this lead') }">
    <template #body-content>
      <FormControl
        v-model="draftAddress"
        type="text"
        :label="__('Address, or a listing link')"
        :placeholder="__('123 Main St, City, ST 55555')"
        autocomplete="off"
      />
    </template>
    <template #actions>
      <Button
        class="w-full"
        variant="solid"
        :label="__('Add & open comps')"
        :loading="adding"
        :disabled="!draftAddress.trim()"
        @click="add"
      />
    </template>
  </Dialog>
</template>

<script setup>
/**
 * Scratch properties hanging off this lead. Optional — a property does not
 * have to be linked, and linking one does not make it a lead.
 */
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import PhotoGalleryModal from '@/components/Modals/PhotoGalleryModal.vue'
import {
  Button,
  Dialog,
  FeatherIcon,
  FormControl,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { useDebounceFn } from '@vueuse/core'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({ lead: { type: String, required: true } })
const router = useRouter()

const list = createResource({
  url: 'crm.api.properties.list_properties',
  params: { lead: props.lead },
  auto: true,
})
const supported = computed(() => !!list.data?.lead_supported)
const rows = computed(() => list.data?.properties || [])

const addOpen = ref(false)
const photosFor = ref(null)
const adding = ref(false)
const draftAddress = ref('')
async function add() {
  if (!draftAddress.value.trim() || adding.value) return
  adding.value = true
  try {
    const res = await call('crm.api.properties.create_property', {
      address: draftAddress.value,
      lead: props.lead,
    })
    addOpen.value = false
    draftAddress.value = ''
    router.push({ name: 'Property', params: { propertyId: res.name } })
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not add the property.'))
  } finally {
    adding.value = false
  }
}

const propOptions = ref([])
const linkKey = ref(0)
const searchProps = useDebounceFn(async (q) => {
  try {
    const found = await call('crm.api.properties.search_properties', { q: q || '' })
    propOptions.value = (found || [])
      .filter((r) => r.lead !== props.lead)
      .map((r) => ({
        label: r.lead
          ? `${r.property_address} · ${__('linked elsewhere')}`
          : r.property_address,
        value: r.name,
      }))
  } catch {
    propOptions.value = []
  }
}, 200)
function onPropQuery(q) {
  searchProps(q)
}
async function linkExisting(opt) {
  const name = opt?.value || ''
  if (!name) return
  try {
    await call('crm.api.properties.set_property_lead', { name, lead: props.lead })
    linkKey.value++
    list.reload()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not link that property.'))
  }
}
</script>
