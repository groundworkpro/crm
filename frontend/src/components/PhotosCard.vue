<template>
  <div class="border-t px-5 py-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
        <CameraIcon class="size-4 text-ink-gray-7" />
        {{ __('Photos') }}
        <span v-if="files.length" class="text-sm font-normal text-ink-gray-5">
          {{ files.length }}
        </span>
      </div>
      <Button
        :tooltip="__('Open photo gallery')"
        icon="image"
        variant="ghost"
        @click="emit('open')"
      />
    </div>

    <!-- A few thumbnails as a teaser; the modal is the real gallery. -->
    <div v-if="files.length" class="mt-2.5 flex flex-col gap-2">
      <div class="grid grid-cols-3 gap-1.5">
        <button
          v-for="(f, i) in preview"
          :key="f.id"
          class="relative aspect-square overflow-hidden rounded bg-surface-gray-2"
          @click="emit('open', i)"
        >
          <img
            :src="f.thumb"
            class="size-full object-cover"
            loading="lazy"
            referrerpolicy="no-referrer"
          />
          <div
            v-if="f.is_video"
            class="absolute inset-0 flex items-center justify-center bg-black/30"
          >
            <FeatherIcon name="play" class="size-4 text-white" />
          </div>
          <!-- "+N more" on the last tile when there are extras -->
          <div
            v-if="i === preview.length - 1 && files.length > preview.length"
            class="absolute inset-0 flex items-center justify-center bg-black/55 text-sm font-medium text-white"
          >
            +{{ files.length - preview.length }}
          </div>
        </button>
      </div>

      <button
        class="flex items-center gap-1.5 text-left text-sm text-ink-blue-link hover:underline"
        @click="openFolder"
      >
        <FeatherIcon name="external-link" class="size-3.5 shrink-0" />
        <span class="truncate">{{ __('Open in Google Drive') }}</span>
      </button>
    </div>

    <div v-else class="mt-2 text-sm text-ink-gray-5">
      {{ __('No photos yet.') }}
    </div>
  </div>
</template>

<script setup>
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import { globalStore } from '@/stores/global'
import { Button, FeatherIcon, createResource } from 'frappe-ui'
import { computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  lead: { type: String, required: true },
})

const emit = defineEmits(['open'])

const { $socket } = globalStore()

const photos = createResource({
  url: 'crm.api.photos.get_lead_photos',
  cache: ['lead_photos', props.lead],
  params: { lead: props.lead },
  auto: true,
})

const files = computed(() => photos.data?.files || [])
const preview = computed(() => files.value.slice(0, 6))

function openFolder() {
  const url = photos.data?.folder?.url
  if (url) window.open(url, '_blank', 'noopener')
}

function onPhotos(data) {
  // A lead or a CRM Property (PROP-…): the name alone is unique across both.
  if (data.reference_docname === props.lead) {
    photos.reload()
  }
}

onMounted(() => $socket.on('crm_photos', onPhotos))
onBeforeUnmount(() => $socket.off('crm_photos', onPhotos))
</script>
