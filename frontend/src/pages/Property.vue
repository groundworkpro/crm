<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs" />
    </template>
    <template #right-header>
      <div class="flex items-center gap-2">
        <!-- Board stage, changeable here so pricing a house and moving it to
             "Offer Sent" does not need a trip back to the board. -->
        <Dropdown v-if="stages.length" :options="stageOptions" placement="bottom-end">
          <Button variant="subtle" :title="__('Change stage')">
            <template #prefix>
              <span class="size-2 rounded-full" :class="stageDot(stage)" />
            </template>
            {{ __(stage) }}
            <template #suffix>
              <FeatherIcon name="chevron-down" class="size-3.5" />
            </template>
          </Button>
        </Dropdown>
        <!-- Phone: Listing / Saved calcs / Edit / Delete in one menu. Five
             labeled buttons beside the breadcrumb is what overflowed the
             viewport and side-scrolled the whole page. -->
        <div class="hidden items-center gap-2 sm:flex">
          <!-- Same 13-steps rail and chat as a lead's comps page (Dennis, 2026-11-05: the AI comp
               tool on a lead's additional properties). -->
          <Button
            v-if="stepsOn"
            :label="railOpen ? __('Hide steps') : __('Steps and chat')"
            variant="subtle"
            iconLeft="list"
            :title="__('The 13 comping steps, and a chat with the agent')"
            @click="railOpen = !railOpen"
          />
          <Button
            v-if="prop.data?.listing_url"
            :label="__('Listing')"
            variant="subtle"
            iconLeft="external-link"
            :title="prop.data.listing_url"
            @click="openListing"
          />
          <Button
            v-if="prop.data?.lead_supported && !prop.data?.lead"
            :label="__('Link lead')"
            variant="subtle"
            iconLeft="user"
            @click="openLink"
          />
          <Button
            v-else-if="prop.data?.lead"
            :label="prop.data.lead_name || __('Lead')"
            variant="subtle"
            iconLeft="user"
            :title="__('Open lead')"
            @click="router.push({ name: 'Lead', params: { leadId: prop.data.lead } })"
          />
          <!-- This house's own Drive photo folder, separate from the lead's
               (Dennis, 2026-11-12: a seller's second house). -->
          <Button
            :label="__('Photos')"
            variant="subtle"
            iconLeft="camera"
            :title="__('Photos of this house, in its own Google Drive folder')"
            @click="showPhotos = true"
          />
          <Button
            :label="offerCount ? __('Saved calcs ({0})', [offerCount]) : __('Saved calcs')"
            variant="subtle"
            iconLeft="clock"
            :disabled="!offerCount"
            @click="showOffers = true"
          />
          <Button
            :label="__('Edit')"
            variant="subtle"
            iconLeft="edit-2"
            @click="openEdit"
          />
          <Button
            variant="subtle"
            theme="red"
            icon="trash-2"
            :title="__('Delete this property')"
            @click="confirmDelete = true"
          />
        </div>
        <Dropdown class="sm:hidden" :options="moreOptions" placement="bottom-end">
          <Button variant="ghost" icon="more-horizontal" :title="__('More')" />
        </Dropdown>
      </div>
    </template>
  </LayoutHeader>

  <!-- Same bounded-height host as pages/Comps.vue: CompsView owns the scrolling. -->
  <div class="flex min-h-0 flex-1 gap-3 overflow-hidden px-3 py-3 sm:px-5 sm:py-4">
    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        v-if="prop.data?.notes"
        class="mb-2 truncate text-xs text-ink-gray-5"
        :title="prop.data.notes"
      >
        {{ prop.data.notes }}
      </div>
      <CompsView
        v-if="propertyId && prop.data"
        :key="mapKey"
        ref="viewRef"
        :lead="propertyId"
        :address="address"
        :prefer-kind="prop.data?.source === 'Auction.com' ? 'auction' : ''"
        page-mode
      />
    </div>
    <CompStepsRail
      v-if="stepsOn && railOpen && prop.data"
      :lead="propertyId"
      :address="address"
      :view="viewRef"
      class="hidden lg:flex"
    />
  </div>

  <!-- Saved calcs, newest first. The latest one already seeds the calculator on
       the map; this is the history — who priced it, when, at what. -->
  <Dialog v-model="showOffers" :options="{ title: __('Saved calcs'), size: 'xl' }">
    <template #body-content>
      <div v-if="offers.length" class="flex flex-col gap-4">
        <div v-for="(o, i) in offers" :key="i">
          <div class="mb-1 text-xs text-ink-gray-5">
            {{ o.by_name }} · {{ formatDate(o.at) }}
          </div>
          <CashOfferComment :html="o.html" :lead="propertyId" @saved="onCalcSaved" />
        </div>
      </div>
      <div v-else class="text-sm text-ink-gray-5">
        {{ __('No calcs saved on this property yet.') }}
      </div>
    </template>
  </Dialog>

  <PhotoGalleryModal
    v-if="showPhotos"
    v-model="showPhotos"
    :lead="propertyId"
    :address="address"
  />

  <Dialog v-model="editOpen" :options="{ title: __('Edit property') }">
    <template #body-content>
      <div class="flex flex-col gap-3">
        <FormControl
          v-model="form.address"
          type="text"
          :label="__('Address, or a Zillow / Redfin / Realtor / Auction.com link')"
          :placeholder="__('123 Main St, City, ST 55555')"
        />
        <FormControl v-model="form.notes" type="textarea" :label="__('Note')" />
        <LeadPicker
          v-if="prop.data?.lead_supported"
          v-model="form.lead"
          :lead-name="form.leadName"
          :label="__('Lead (optional)')"
          @update:lead-name="form.leadName = $event"
        />
        <p v-if="addressChanged" class="text-xs text-ink-amber-9">
          {{ __('Changing the address looks the new house up fresh (geocode, Zillow, comps). Picks and calcs stay.') }}
        </p>
      </div>
    </template>
    <template #actions>
      <Button
        class="w-full"
        variant="solid"
        :label="__('Save')"
        :loading="saving"
        :disabled="!form.address.trim()"
        @click="saveEdit"
      />
    </template>
  </Dialog>

  <Dialog v-model="linkOpen" :options="{ title: __('Link a lead') }">
    <template #body-content>
      <p class="mb-3 text-sm text-ink-gray-6">
        {{ __('This house stays a property. Linking just hangs it off a seller.') }}
      </p>
      <LeadPicker
        :model-value="linkLead"
        :lead-name="linkLeadName"
        @update:model-value="saveLink"
        @update:lead-name="linkLeadName = $event"
      />
    </template>
  </Dialog>

  <Dialog v-model="confirmDelete" :options="{ title: __('Delete this property?') }">
    <template #body-content>
      <div class="text-sm text-ink-gray-7">
        {{ __('Deletes the property, its comp picks, and every saved calc. A linked lead is not deleted.') }}
      </div>
    </template>
    <template #actions>
      <Button
        class="w-full"
        variant="solid"
        theme="red"
        :label="__('Delete')"
        :loading="deleting"
        @click="remove"
      />
    </template>
  </Dialog>
</template>

<script setup>
/**
 * One scratch property: the real comps page (CompsView, page mode) pointed at a
 * `CRM Property` instead of a lead. Every pick, hide, condition tag, sqft
 * override and saved calc writes to the property; the backend dispatches on the
 * `PROP-` prefix, so the map component is byte-identical to the lead's.
 */
import LayoutHeader from '@/components/LayoutHeader.vue'
import CompsView from '@/components/CompsView.vue'
import CompStepsRail from '@/components/CompStepsRail.vue'
import CashOfferComment from '@/components/Activities/CashOfferComment.vue'
import LeadPicker from '@/components/LeadPicker.vue'
import PhotoGalleryModal from '@/components/Modals/PhotoGalleryModal.vue'
import { sidebarCollapsedOverride } from '@/composables/settings'
import { formatDate } from '@/utils'
import { stageDot } from '@/utils/propertyStages'
import {
  Breadcrumbs,
  Button,
  Dialog,
  Dropdown,
  FeatherIcon,
  FormControl,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({ propertyId: { type: String, required: true } })
const router = useRouter()

// The 13-steps rail, for the people Ask AI is turned on for (as on a lead's comps page).
const viewRef = ref(null)
const stepsOn = ref(false)
const railOpen = ref(false)
call('crm.api.ai_pricer.status').then((r) => (stepsOn.value = !!r?.enabled)).catch(() => {})

// Same override the lead comps page uses: this is a map, and every pixel the
// nav holds is one the map does not get. Not a write to the stored preference.
onMounted(() => {
  sidebarCollapsedOverride.value = true
})
onUnmounted(() => {
  sidebarCollapsedOverride.value = null
})

const prop = createResource({
  url: 'crm.api.properties.get_property',
  params: { name: props.propertyId },
  auto: true,
  onSuccess: (d) => {
    if (d?.property_address) document.title = `Comps — ${d.property_address}`
  },
})

const address = computed(() => prop.data?.property_address || '')
const offers = computed(() => prop.data?.offers || [])
const showPhotos = ref(false)
const offerCount = computed(() => offers.value.length)
// Bumped on an address change so CompsView (which loads onMounted and has no
// watcher on its `lead` prop) re-mounts against the new house.
const mapGen = ref(0)
const mapKey = computed(() => `${props.propertyId}:${mapGen.value}`)

const breadcrumbs = computed(() => [
  { label: __('Properties'), route: { name: 'Properties' } },
  { label: address.value || props.propertyId },
])

const moreOptions = computed(() => {
  const items = []
  if (prop.data?.listing_url) {
    items.push({
      label: __('Listing'),
      icon: 'external-link',
      onClick: openListing,
    })
  }
  items.push({
    label: __('Photos'),
    icon: 'camera',
    onClick: () => (showPhotos.value = true),
  })
  items.push({
    label: offerCount.value
      ? __('Saved calcs ({0})', [offerCount.value])
      : __('Saved calcs'),
    icon: 'clock',
    onClick: () => {
      if (offerCount.value) showOffers.value = true
    },
    disabled: !offerCount.value,
  })
  if (prop.data?.lead) {
    items.push({
      label: prop.data.lead_name || __('Open lead'),
      icon: 'user',
      onClick: () => router.push({ name: 'Lead', params: { leadId: prop.data.lead } }),
    })
  } else if (prop.data?.lead_supported) {
    items.push({
      label: __('Link lead'),
      icon: 'user',
      onClick: openLink,
    })
  }
  items.push({ label: __('Edit'), icon: 'edit-2', onClick: openEdit })
  items.push({
    label: __('Delete'),
    icon: 'trash-2',
    onClick: () => {
      confirmDelete.value = true
    },
  })
  return items
})

function openListing() {
  const url = prop.data?.listing_url
  if (!url) return
  const win = window.open(url, '_blank', 'noopener')
  if (win) win.opener = null
}

const showOffers = ref(false)
function onCalcSaved() {
  prop.reload()
}

// --- stage ------------------------------------------------------------------
const stages = computed(() => prop.data?.stages || [])
const stage = computed(() => prop.data?.status || stages.value[0] || '')
const stageOptions = computed(() =>
  stages.value.map((s) => ({ label: __(s), onClick: () => setStage(s) })),
)
async function setStage(s) {
  if (!prop.data || s === prop.data.status) return
  const before = prop.data.status
  prop.data.status = s
  try {
    await call('crm.api.properties.set_property_status', { name: props.propertyId, status: s })
  } catch (e) {
    prop.data.status = before
    toast.error(e.messages?.[0] || __('Could not change the stage.'))
  }
}

// --- edit -------------------------------------------------------------------
const editOpen = ref(false)
const saving = ref(false)
const form = reactive({ address: '', notes: '', lead: '', leadName: '' })
const addressChanged = computed(
  () => form.address.trim() !== (prop.data?.property_address || '').trim(),
)
function openEdit() {
  form.address = prop.data?.property_address || ''
  form.notes = prop.data?.notes || ''
  form.lead = prop.data?.lead || ''
  form.leadName = prop.data?.lead_name || ''
  editOpen.value = true
}
async function saveEdit() {
  if (!form.address.trim() || saving.value) return
  saving.value = true
  const changed = addressChanged.value
  try {
    const d = await call('crm.api.properties.update_property', {
      name: props.propertyId,
      address: form.address,
      notes: form.notes,
      lead: form.lead || '',
    })
    prop.data = d
    editOpen.value = false
    if (changed) mapGen.value++
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not save.'))
  } finally {
    saving.value = false
  }
}
const linkOpen = ref(false)
const linking = ref(false)
const linkLead = ref('')
const linkLeadName = ref('')
function openLink() {
  linkLead.value = ''
  linkLeadName.value = ''
  linkOpen.value = true
}
async function saveLink(name) {
  if (!name || linking.value) return
  linking.value = true
  try {
    const d = await call('crm.api.properties.set_property_lead', {
      name: props.propertyId,
      lead: name,
    })
    prop.data = d
    linkOpen.value = false
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not link that lead.'))
  } finally {
    linking.value = false
  }
}
async function unlinkLead() {
  if (!prop.data?.lead) return
  const before = { lead: prop.data.lead, lead_name: prop.data.lead_name }
  prop.data.lead = ''
  prop.data.lead_name = ''
  try {
    const d = await call('crm.api.properties.set_property_lead', {
      name: props.propertyId,
      lead: '',
    })
    prop.data = d
  } catch (e) {
    prop.data.lead = before.lead
    prop.data.lead_name = before.lead_name
    toast.error(e.messages?.[0] || __('Could not unlink.'))
  }
}

// --- delete -----------------------------------------------------------------
const confirmDelete = ref(false)
const deleting = ref(false)
async function remove() {
  deleting.value = true
  try {
    await call('crm.api.properties.delete_property', { name: props.propertyId })
    router.replace({ name: 'Properties' })
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not delete.'))
  } finally {
    deleting.value = false
  }
}
</script>
