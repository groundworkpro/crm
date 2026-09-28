<template>
  <Dialog
    v-model="show"
    :options="{
      title: editMode ? __('Edit contractor') : __('New contractor'),
      size: 'xl',
    }"
  >
    <template #body-content>
      <div class="flex flex-col gap-4">
        <div class="grid grid-cols-2 gap-4">
          <FormControl
            v-model="form.contractor_name"
            :label="__('Name')"
            :placeholder="__('Jane Smith')"
            required
          />
          <FormControl
            v-model="form.company"
            :label="__('Company')"
            :placeholder="__('Optional')"
          />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormControl
            v-model="form.phone"
            :label="__('Phone')"
            :placeholder="__('(719) 555-0123')"
          />
          <FormControl
            v-model="form.email"
            type="email"
            :label="__('Email')"
            :placeholder="__('name@example.com')"
          />
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs text-ink-gray-5">{{ __('Does') }}</label>
          <div class="flex gap-5">
            <FormControl v-model="form.does_photos" type="checkbox" :label="__('Photos')" />
            <FormControl v-model="form.does_lockbox" type="checkbox" :label="__('Lockbox install')" />
          </div>
        </div>

        <!-- metro areas: the same Census list buyers use; a property matches
             when its county is in one of these metros -->
        <div class="space-y-1.5">
          <label class="block text-xs text-ink-gray-5">
            {{ __('Metro areas they cover') }}
          </label>
          <div v-if="form.metro_areas.length" class="flex flex-wrap gap-1">
            <span
              v-for="m in form.metro_areas"
              :key="m"
              class="flex items-center gap-1 rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-7"
            >
              {{ m }}
              <button
                type="button"
                class="text-ink-gray-4 hover:text-ink-gray-7"
                @click="removeMetro(m)"
              >
                ✕
              </button>
            </span>
          </div>
          <Autocomplete
            :options="metroOptions"
            :modelValue="''"
            :placeholder="__('Add a metro…')"
            @update:modelValue="addMetro"
          />
        </div>

        <FormControl
          v-model="form.notes"
          type="textarea"
          :label="__('Notes')"
          :placeholder="__('Rates, reliability, how they like to be paid…')"
        />

        <FormControl
          v-if="editMode"
          v-model="form.active"
          type="checkbox"
          :label="__('Active (uncheck to stop suggesting them)')"
        />

        <ErrorMessage :message="error" />

        <div class="flex items-center justify-between">
          <Button
            v-if="editMode"
            variant="ghost"
            theme="red"
            :label="confirmDelete ? __('Click again to delete') : __('Delete')"
            @click="remove"
          />
          <span v-else />
          <Button
            variant="solid"
            :label="editMode ? __('Save') : __('Create')"
            :loading="saving"
            @click="submit"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import { Dialog, FormControl, ErrorMessage, Button, call, createResource } from 'frappe-ui'
import { ref, watch, computed } from 'vue'

const props = defineProps({
  // a row from get_contractors to edit; omit to create
  contractor: { type: Object, default: null },
  // pre-picked metro for a new contractor (e.g. opened from a board card)
  metro: { type: String, default: '' },
})
const emit = defineEmits(['saved'])
const show = defineModel()

const editMode = computed(() => !!props.contractor?.name)

const blank = () => ({
  contractor_name: '',
  company: '',
  phone: '',
  email: '',
  does_photos: true,
  does_lockbox: true,
  metro_areas: props.metro ? [props.metro] : [],
  notes: '',
  active: true,
})
const form = ref(blank())
const error = ref('')
const saving = ref(false)
const confirmDelete = ref(false)

watch(show, (v) => {
  if (!v) return
  error.value = ''
  confirmDelete.value = false
  const c = props.contractor
  form.value = c
    ? {
        contractor_name: c.contractor_name || '',
        company: c.company || '',
        phone: c.phone || '',
        email: c.email || '',
        does_photos: !!c.does_photos,
        does_lockbox: !!c.does_lockbox,
        metro_areas: [...(c.metros || [])],
        notes: c.notes || '',
        active: !!c.active,
      }
    : blank()
})

const metros = createResource({ url: 'crm.api.buyers.get_metro_areas', auto: true })
const metroOptions = computed(() =>
  (metros.data || [])
    .filter((m) => !form.value.metro_areas.includes(m.name))
    .map((m) => ({ label: m.metro_name, value: m.name })),
)

function addMetro(option) {
  const name = option?.value
  if (name && !form.value.metro_areas.includes(name)) form.value.metro_areas.push(name)
}

function removeMetro(name) {
  form.value.metro_areas = form.value.metro_areas.filter((m) => m !== name)
}

async function submit() {
  error.value = ''
  if (!form.value.contractor_name.trim()) {
    error.value = __('Name is required.')
    return
  }
  saving.value = true
  try {
    const values = {
      ...form.value,
      does_photos: form.value.does_photos ? 1 : 0,
      does_lockbox: form.value.does_lockbox ? 1 : 0,
      active: form.value.active ? 1 : 0,
    }
    const r = await call('crm.api.contractors.save_contractor', {
      values: JSON.stringify(values),
      contractor: props.contractor?.name || null,
    })
    show.value = false
    emit('saved', r.name)
  } catch (e) {
    error.value = e.messages?.[0] || e.message
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!confirmDelete.value) {
    confirmDelete.value = true
    return
  }
  try {
    await call('crm.api.contractors.delete_contractor', {
      contractor: props.contractor.name,
    })
    show.value = false
    emit('saved')
  } catch (e) {
    error.value = e.messages?.[0] || e.message
  }
}
</script>
