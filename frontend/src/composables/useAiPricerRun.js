// The comps page opened from an Ask AI result ("Finish on comps page" links with
// ?ai=<job id>): load that run, match the AI's comps to the board's rows, pick
// them once, and hand the AI's ARV + repair tier to the calculator once.
import { computed, ref, watch } from 'vue'
import { call } from 'frappe-ui'
import { addressMatchKey } from '@/utils/comps'

// comping/oneshot.py's TIERS, $/sqft.
const TIER_PSF = { clean: 5, paint_carpet: 10, kitchen_baths: 30, full_rehab: 50, studs: 75 }

export function useAiPricerRun({ job, rows, ready, pick, prefill }) {
  const run = ref(null)

  watch(
    job,
    async (id) => {
      run.value = null
      if (!id) return
      try {
        const r = await call('crm.api.ai_pricer.get_job', { job: id })
        if (r?.status === 'done' && r.answer?.arv) run.value = r.answer
      } catch {
        /* expired or offline: the page still works without it */
      }
    },
    { immediate: true },
  )

  // comp name -> the AI's line for it; plus the AI comps the board does not have.
  const matched = computed(() => {
    const byKey = new Map()
    for (const c of rows()) {
      const k = addressMatchKey(c.address)
      if (k && !byKey.has(k)) byKey.set(k, c)
    }
    const used = {}
    const missing = []
    for (const a of run.value?.comps_used || []) {
      const c = byKey.get(addressMatchKey(a.address))
      if (c) used[c.name] = a
      else missing.push(a)
    }
    return { used, missing }
  })

  // Once per job per browser tab: a reload must not re-pick a comp the rep has
  // since dropped, or overwrite numbers they have since changed.
  watch([run, ready], async ([r, ok]) => {
    const key = `aiPricerApplied:${job()}`
    if (!r || !ok || sessionStorage.getItem(key)) return
    sessionStorage.setItem(key, '1')
    prefill({
      arv: Number(r.arv) || 0,
      rehabPsf: TIER_PSF[r.repair_tier] || TIER_PSF.kitchen_baths,
      mult: r.offers?.recommend === 'classic' ? 1 : 2,
    })
    const want = rows().filter((c) => matched.value.used[c.name] && !c.selected)
    // One at a time: set_comp_state is read-modify-write on the lead's list.
    for (const c of want) await pick(c.name)
  })

  return { run, used: computed(() => matched.value.used), missing: computed(() => matched.value.missing) }
}
