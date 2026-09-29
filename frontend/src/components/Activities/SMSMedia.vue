<template>
  <div v-if="media?.length" class="flex flex-col gap-1.5">
    <template v-for="(m, i) in media" :key="i">
      <!-- images: inline thumbnail, click opens the pageable lightbox -->
      <div v-if="isImage(m)" class="group relative min-h-16 min-w-16 max-w-64">
        <img
          :src="m.url"
          loading="lazy"
          class="max-h-64 max-w-full cursor-zoom-in rounded-md object-cover"
          @click="openAt(m)"
        />
        <!-- save to the lead's Photos folder (only when a lead is given) -->
        <template v-if="saves.enabled">
          <span
            v-if="saves.isSaved(m)"
            class="pointer-events-none absolute bottom-1.5 right-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white"
          >
            ✓ {{ __('Saved') }}
          </span>
          <span
            v-else-if="saves.isPending(m)"
            class="pointer-events-none absolute bottom-1.5 right-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white"
          >
            {{ __('Saving…') }}
          </span>
          <button
            v-else
            type="button"
            class="absolute bottom-1.5 right-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white opacity-0 transition-opacity hover:bg-black/80 group-hover:opacity-100"
            @click.stop="saves.save([{ url: m.url }])"
          >
            {{ __('Save to Photos') }}
          </button>
        </template>
      </div>
      <!-- videos: inline player with controls -->
      <video
        v-else-if="isVideo(m)"
        :src="m.url"
        controls
        preload="metadata"
        class="max-h-64 max-w-full rounded-md"
      />
      <!-- anything else (audio, pdf, vcard…): a labeled download link -->
      <a
        v-else
        :href="m.url"
        target="_blank"
        rel="noopener noreferrer"
        class="inline-flex items-center gap-1 text-sm underline"
      >
        <AttachmentIcon class="size-3.5" />
        {{ label(m) }}
      </a>
    </template>

    <!-- full-screen viewer; arrows/keyboard page through this message's images -->
    <ImageLightbox
      v-if="viewerOpen"
      :images="imageUrls"
      :start-index="viewerIndex"
      @close="viewerOpen = false"
    />
  </div>
</template>

<script setup>
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import ImageLightbox from '@/components/Activities/ImageLightbox.vue'
import { useLeadPhotoSaves } from '@/composables/leadPhotoSaves'
import { computed, ref } from 'vue'

const props = defineProps({
  // [{ url, type }] — `type` is the MMS mime type from Quo (e.g. image/jpeg)
  media: { type: Array, default: () => [] },
  // CRM Lead name; when set, each picture gets a "Save to Photos" button
  lead: { type: String, default: '' },
})

const saves = useLeadPhotoSaves(props.lead)

function isImage(m) {
  return (m.type || '').startsWith('image/')
}

function isVideo(m) {
  return (m.type || '').startsWith('video/')
}

function label(m) {
  return m.type || __('Attachment')
}

// only images are pageable in the lightbox (videos/files keep inline behavior)
const imageUrls = computed(() => props.media.filter(isImage).map((m) => m.url))
const viewerOpen = ref(false)
const viewerIndex = ref(0)

function openAt(m) {
  const idx = imageUrls.value.indexOf(m.url)
  viewerIndex.value = idx < 0 ? 0 : idx
  viewerOpen.value = true
}
</script>
