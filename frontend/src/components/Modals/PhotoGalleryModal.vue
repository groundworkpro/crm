<template>
  <Dialog v-model="show" :options="{ size: '4xl' }">
    <template #body>
      <div class="flex flex-col gap-4 p-5">
        <!-- header -->
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2 text-lg font-semibold text-ink-gray-9">
              <CameraIcon class="size-4.5 shrink-0 text-ink-gray-7" />
              {{ __('Property Photos') }}
            </div>
            <div class="mt-0.5 truncate text-sm text-ink-gray-5">
              {{ folder?.name || address || __('No property address set') }}
              <span v-if="files.length"> · {{ files.length }} {{ __('files') }}</span>
            </div>
          </div>
          <Button variant="ghost" icon="x" @click="show = false" />
        </div>

        <!-- actions -->
        <div class="flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            icon-left="upload"
            :label="__('Add photos')"
            :loading="uploading"
            :disabled="!address"
            @click="pick"
          />
          <Button
            icon-left="download"
            :label="__('Download all')"
            :disabled="!files.length || zipping"
            :loading="zipping"
            @click="downloadAll"
          />
          <Button
            icon-left="link"
            :label="__('Copy folder link')"
            :disabled="!address"
            :loading="linking"
            @click="copyFolderLink"
          />
          <Button
            v-if="folder"
            icon-left="external-link"
            :label="__('Open in Drive')"
            @click="openFolder"
          />
        </div>

        <div
          v-if="uploading"
          class="rounded-md bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-7"
        >
          {{ __('Uploading') }} {{ done + 1 }} / {{ queued }}…
          <span v-if="failed" class="text-ink-red-3"> · {{ failed }} {{ __('failed') }}</span>
        </div>

        <ErrorMessage :message="error" />

        <!-- gallery -->
        <div
          class="max-h-[60vh] min-h-[12rem] overflow-y-auto rounded-md"
          :class="dragging ? 'ring-2 ring-outline-blue-2' : ''"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <div v-if="loading" class="py-10 text-center text-sm text-ink-gray-5">
            {{ __('Loading photos…') }}
          </div>

          <div
            v-else-if="!files.length"
            class="flex flex-col items-center justify-center gap-2 py-12 text-center"
          >
            <CameraIcon class="size-8 text-ink-gray-4" />
            <div class="text-sm text-ink-gray-6">
              {{ __('No photos yet — drop files here or use Add photos.') }}
            </div>
            <div v-if="!address" class="text-sm text-ink-red-3">
              {{ __('Set a property address first — the folder is named after it.') }}
            </div>
          </div>

          <div v-else class="grid grid-cols-2 gap-2 sm:grid-cols-4 md:grid-cols-5">
            <div
              v-for="(f, i) in files"
              :key="f.id"
              class="group relative aspect-square overflow-hidden rounded-md bg-surface-gray-2"
            >
              <img
                :src="f.thumb"
                class="size-full cursor-pointer object-cover transition group-hover:opacity-90"
                loading="lazy"
                referrerpolicy="no-referrer"
                @click="openViewer(f, i)"
              />
              <div
                v-if="f.is_video"
                class="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/25"
              >
                <FeatherIcon name="play" class="size-6 text-white" />
              </div>
              <!-- hover-only affordances, matching the kanban card pattern -->
              <button
                class="absolute right-1 top-1 hidden rounded-full bg-black/60 p-1.5 text-white hover:bg-black/80 group-hover:block"
                :title="__('Remove')"
                @click.stop="remove(f)"
              >
                <FeatherIcon name="trash-2" class="size-3.5" />
              </button>
              <div
                class="pointer-events-none absolute inset-x-0 bottom-0 hidden truncate bg-black/55 px-1.5 py-1 text-xs text-white group-hover:block"
              >
                {{ f.name }}
              </div>
            </div>
          </div>
        </div>

        <input
          ref="fileInput"
          type="file"
          class="hidden"
          multiple
          accept="image/*,video/*"
          @change="onPicked"
        />
      </div>
    </template>
  </Dialog>

  <ImageLightbox
    v-if="viewer.open"
    :images="viewerImages"
    :start-index="viewer.index"
    @close="viewer.open = false"
  />
</template>

<script setup>
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import ImageLightbox from '@/components/Activities/ImageLightbox.vue'
import { copyToClipboard } from '@/utils'
import {
  call,
  Dialog,
  Button,
  ErrorMessage,
  FeatherIcon,
  createResource,
  toast,
} from 'frappe-ui'
import { ref, computed, reactive, watch } from 'vue'

const props = defineProps({
  lead: { type: String, required: true },
  address: { type: String, default: '' },
})

const show = defineModel({ type: Boolean })

const fileInput = ref(null)
const uploading = ref(false)
const zipping = ref(false)
const linking = ref(false)
const dragging = ref(false)
const error = ref(null)
const queued = ref(0)
const done = ref(0)
const failed = ref(0)

const viewer = reactive({ open: false, index: 0 })

const photos = createResource({
  url: 'crm.api.photos.get_lead_photos',
  cache: ['lead_photos', props.lead],
  params: { lead: props.lead },
})

const loading = computed(() => photos.loading)
const files = computed(() => photos.data?.files || [])
const folder = computed(() => photos.data?.folder || null)

// The lightbox only knows how to page through still images.
const images = computed(() => files.value.filter((f) => !f.is_video))
const viewerImages = computed(() => images.value.map((f) => f.full))

watch(
  show,
  (open) => {
    if (!open) return
    error.value = null
    photos.reload()
  },
  { immediate: true },
)

function openViewer(f, i) {
  if (f.is_video) {
    window.open(f.view, '_blank', 'noopener')
    return
  }
  viewer.index = images.value.findIndex((x) => x.id === f.id)
  if (viewer.index < 0) viewer.index = 0
  viewer.open = true
}

function pick() {
  fileInput.value?.click()
}

function onPicked(e) {
  const list = Array.from(e.target.files || [])
  e.target.value = '' // let the same file be re-picked later
  upload(list)
}

function onDrop(e) {
  dragging.value = false
  upload(Array.from(e.dataTransfer?.files || []))
}

async function upload(list) {
  if (!list.length || uploading.value) return
  if (!props.address) {
    error.value = __('Set a property address first.')
    return
  }
  error.value = null
  uploading.value = true
  queued.value = list.length
  done.value = 0
  failed.value = 0

  // One request per file: a phone batch is hundreds of MB, and a single giant
  // POST is what trips nginx's body limit and loses the whole batch at once.
  for (const file of list) {
    try {
      const form = new FormData()
      form.append('lead', props.lead)
      form.append('file', file)
      const res = await fetch(
        '/api/method/crm.api.photos.upload_lead_photo',
        {
          method: 'POST',
          headers: window.csrf_token
            ? { 'X-Frappe-CSRF-Token': window.csrf_token }
            : {},
          body: form,
        },
      )
      if (!res.ok) throw new Error(await res.text())
    } catch (e) {
      failed.value += 1
    }
    done.value += 1
  }

  uploading.value = false
  await photos.reload()

  if (failed.value)
    toast.error(__('{0} file(s) failed to upload', [failed.value]))
  else toast.success(__('Photos added'))
}

async function remove(f) {
  try {
    await call('crm.api.photos.delete_lead_photo', {
      lead: props.lead,
      file_id: f.id,
    })
    await photos.reload()
    toast.success(__('Removed (recoverable from Drive’s bin)'))
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not remove that file'))
  }
}

function downloadAll() {
  if (!files.value.length) return
  zipping.value = true
  // A plain navigation: the endpoint replies with a download response.
  window.open(
    `/api/method/crm.api.photos.download_all_photos?lead=${encodeURIComponent(props.lead)}`,
    '_blank',
  )
  setTimeout(() => (zipping.value = false), 1500)
}

async function copyFolderLink() {
  linking.value = true
  try {
    // Creates/adopts the folder if it doesn't exist yet, so the link always works.
    const f = folder.value || (await call('crm.api.photos.ensure_photo_folder', {
      lead: props.lead,
    }))
    copyToClipboard(f.url)
    if (!folder.value) photos.reload()
  } catch (e) {
    error.value = e.messages?.[0] || __('Could not create the photo folder')
  } finally {
    linking.value = false
  }
}

function openFolder() {
  if (folder.value?.url) window.open(folder.value.url, '_blank', 'noopener')
}
</script>
