// The 13 comp steps, shared by the comps page rail and the AVM training page.
export const STEP_TITLES = ['Subject facts', 'Area price feel', 'Ceiling', 'Same side of road and river', 'Similar size, type, era',
  'Condition of each comp', 'Sold, days on market, recency', 'Flip history', 'Best comp is the anchor', 'Second comp to balance',
  'Size adjustment, conservative', 'Repairs', 'Save the calcs']
// Every step takes any tool. 6, 9 and 12 start on Jev; the rest start on their code rule. STEP_CALLS is the one
// judgment an AI makes on a rule step (comping/decide.py STEP_CALLS). Plain strings: window.__ is not set yet at import.
export const JEV_STEPS = [6, 9, 12]
export const STEP_CALLS = {
  1: 'flag the subject for a person, or not', 2: 'shade older sales for a falling market, or not',
  3: 'which ceiling caps the value', 4: 'whether each road or river really splits the neighborhood',
  5: 'keep comps just outside the size band', 6: 'the condition of each comp', 7: 'which radius to start from',
  8: 'read each repeat sale: fixed up in between or not', 9: 'which comp is the anchor',
  10: 'keep or drop each support comp', 11: 'how to blend the comps into one value',
  12: 'the repair tier', 13: 'which offer to recommend',
}
export const MODELS = ['anthropic/claude-haiku-5-5', 'anthropic/claude-sonnet-5-5', 'openai-codex/gpt-6-luna', 'antigravity/gemini-3-8-flash',
  'inco/deepseek-v4.1-flash', 'hf/Qwen/Qwen3-235B-A22B-Instruct-2507']


export const  toolLabel = (t) => (t === 'jev' ? 'Jev' : t === 'code' ? __('Rule') : t.split(':').pop().split('/').pop())
export const  defaultTool = (n) => (JEV_STEPS.includes(n) ? 'jev' : 'code')
