<template>
  <div
    class="email-content prose-f max-w-none break-words py-2 text-p-sm text-ink-gray-8"
    v-html="content"
  />
</template>

<script setup>
defineProps({
  content: { type: String, required: true },
})
</script>
