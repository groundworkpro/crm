<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs" />
    </template>
    <template #right-header>
      <Button
        v-if="stepsOn"
        :label="railOpen ? __('Hide steps') : __('Steps and chat')"
        iconLeft="list"
        :title="__('The 13 comping steps, and a chat with the agent') + ' (T, /)'"
        @click="railOpen = !railOpen"
      />
      <Button
        v-if="lead"
        :label="__('Open lead')"
        iconLeft="external-link"
        @click="openLead"
      />
    </template>
  </LayoutHeader>

  <!-- Bounded height, always. CompsView owns scrolling (overflow-y-auto with
       a floored map in both layouts) so a tall calc or an unfolded Tax /
       liens card cannot clip anything. Hosts that scroll of their own (the
       old comps page) made the phone paint a desktop form with a thumbnail
       map. -->
  <div class="flex min-h-0 flex-1 gap-3 overflow-hidden px-3 py-3 sm:px-5 sm:py-4">
    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <CompsView v-if="leadId" ref="viewRef" :lead="leadId" :address="address" page-mode />
    </div>
    <CompStepsRail v-if="stepsOn && railOpen" ref="railRef" :lead="leadId" :address="address" :view="viewRef" class="hidden lg:flex" />
  </div>
</template>

<script setup>
/**
 * The comps map as its own PAGE, opened in a new tab from the lead.
 *
 * It was a modal until reps started actually underwriting from it: a modal is
 * the wrong container for something you sit with, cross-reference against the
 * lead, and pick comps in. As a tab it can be left open beside the lead, and it
 * gets the room for the property list under the map.
 */
import LayoutHeader from '@/components/LayoutHeader.vue'
import CompsView from '@/components/CompsView.vue'
import CompStepsRail from '@/components/CompStepsRail.vue'
import { Breadcrumbs, Button, call, createResource } from 'frappe-ui'
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useRouter } from 'vue-router'
import { sidebarCollapsedOverride } from '@/composables/settings'

const props = defineProps({ leadId: { type: String, required: true } })
const router = useRouter()

// The 13-steps rail, for the people Ask AI is turned on for. T opens Steps, / opens Chat.
const viewRef = ref(null)
const railRef = ref(null)
const stepsOn = ref(false)
const railOpen = ref(false)
call('crm.api.ai_pricer.status').then((r) => (stepsOn.value = !!r?.enabled)).catch(() => {})
async function openRail(tab) {
  railOpen.value = true
  await nextTick()
  railRef.value?.show(tab)
}
useKeyboardShortcuts({
  active: () => stepsOn.value,
  shortcuts: [
    { keys: ['t', 'T'], action: () => openRail('steps') },
    { keys: '/', action: () => openRail('chat') },
  ],
})

// This page is a map. Every pixel the nav holds is a pixel the map does not get,
// and the page is opened in its own tab from the lead -- nobody arrives here to
// navigate somewhere else. An OVERRIDE rather than a write to the stored
// preference, so closing this tab cannot leave the rest of the CRM collapsed;
// hitting Expand still works and, being a deliberate act, wins from then on.
onMounted(() => {
  sidebarCollapsedOverride.value = true
})
onUnmounted(() => {
  sidebarCollapsedOverride.value = null
})

// Only the address is needed for the header/title; the map fetches its own data.
const lead = createResource({
  url: 'frappe.client.get_value',
  params: {
    doctype: 'CRM Lead',
    filters: { name: props.leadId },
    fieldname: ['name', 'lead_name', 'property_address'],
  },
  auto: true,
  onSuccess: (d) => {
    if (d?.property_address) document.title = `Comps — ${d.property_address}`
  },
})

const address = computed(() => lead.data?.property_address || '')

const breadcrumbs = computed(() => [
  { label: __('Leads'), route: { name: 'Leads' } },
  {
    label: lead.data?.lead_name || props.leadId,
    route: { name: 'Lead', params: { leadId: props.leadId } },
  },
  { label: __('Comps') },
])

function openLead() {
  router.push({ name: 'Lead', params: { leadId: props.leadId } })
}
</script>
