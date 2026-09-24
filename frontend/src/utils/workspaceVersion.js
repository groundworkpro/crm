// Workspace version switch: "classic" (today's CRM, byte-identical) vs "next"
// (the Telnyx phone dock + the left-nav comms that replace Mattermost).
//
// PREVIEW: the choice lives in memory on the phone preview state
// (`phone.workspace`) and applying "next" simply turns on the existing mockup
// pieces — phone design A, comms design D, Talk routes.
//
// REAL MECHANISM (not wired here, on purpose): a per-user Frappe default
// `crm_workspace_version` ('classic' | 'next'), the same no-doctype trick the
// task due chips and text presets use. It is read once at boot alongside the
// session/user store (`stores/users.js` `getUser()` → user defaults) and set by
// this menu item through `frappe.client.set_default`. Nothing in the preview
// writes to the server; `WORKSPACE_DEFAULT_KEY` is exported so the real read
// and write share one spelling when that lands.
import { newPhonePreview, selectPhoneDesign } from './phonePreview.js'
import { newCommsPreview, selectCommsDesign } from './commsPreview.js'

export const WORKSPACE_DEFAULT_KEY = 'crm_workspace_version'
export const workspaceVersions = { classic: 'Classic', next: 'New workspace' }
export const NEXT_PHONE_DESIGN = 'A'
export const NEXT_COMMS_DESIGN = 'D'

// The `?phonePreview=1` query is a dev override: any preview on = "next" as far
// as the switch is concerned, so "Back to classic" always means the real layout.
export function currentWorkspace(phone) {
  return phone.workspace === 'next' || phone.enabled ? 'next' : 'classic'
}
export function workspaceMenuLabel(phone) {
  return currentWorkspace(phone) === 'next' ? 'Back to classic' : 'Try the new workspace'
}

export function applyWorkspace(phone, comms, version) {
  if (!Object.hasOwn(workspaceVersions, version)) return false
  if (version === 'classic') {
    // Classic = production. Reset both states so no component renders.
    Object.assign(phone, newPhonePreview(), { workspace: 'classic', workspaceBannerSeen: phone.workspaceBannerSeen })
    Object.assign(comms, newCommsPreview())
    return true
  }
  const firstTime = !phone.workspaceBannerSeen
  phone.enabled = true
  phone.workspace = 'next'
  selectPhoneDesign(phone, NEXT_PHONE_DESIGN)
  phone.minimized = true
  selectCommsDesign(comms, NEXT_COMMS_DESIGN)
  phone.workspaceBanner = firstTime
  phone.workspaceBannerSeen = true
  return true
}

export function toggleWorkspace(phone, comms) {
  return applyWorkspace(phone, comms, currentWorkspace(phone) === 'next' ? 'classic' : 'next')
}

// The query that keeps "next" on across a reload, until the real user default exists.
export function nextWorkspaceQuery(query = {}) {
  return { ...query, phonePreview: '1', phoneDesign: NEXT_PHONE_DESIGN, commsDesign: NEXT_COMMS_DESIGN }
}
export function classicWorkspaceQuery(query = {}) {
  const rest = { ...query }
  delete rest.phonePreview; delete rest.phoneDesign; delete rest.commsDesign; delete rest.phoneIncoming
  return rest
}
