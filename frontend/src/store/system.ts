import { defineStore } from 'pinia'
import { apiGet, apiPost, apiPut, statusWsUrl } from '../api/request'
import type { RuntimeConfig, SystemStatus } from '../types/system'

export const useSystemStore = defineStore('system', {
  state: () => ({
    status: null as SystemStatus | null,
    config: null as RuntimeConfig | null,
    selectedSlot: null as string | null,
    connected: false,
    error: '',
    busy: false
  }),
  actions: {
    async refresh() {
      try {
        this.status = await apiGet<SystemStatus>('/api/system/status')
        this.config = await apiGet<RuntimeConfig>('/api/config')
        this.error = ''
      } catch (err: any) {
        this.error = err?.message || String(err)
      }
    },
    connectWs() {
      const ws = new WebSocket(statusWsUrl())
      ws.onopen = () => { this.connected = true }
      ws.onclose = () => { this.connected = false; setTimeout(() => this.connectWs(), 3000) }
      ws.onerror = () => { this.connected = false }
      ws.onmessage = (ev) => { this.status = JSON.parse(ev.data) as SystemStatus }
    },
    async pipeline(action: 'start' | 'stop' | 'restart') {
      this.busy = true
      try {
        await apiPost(`/api/pipeline/${action}`)
        await this.refresh()
      } catch (err: any) {
        this.error = err?.message || String(err)
      } finally {
        this.busy = false
      }
    },
    async slotCommand(slot: string, action: string) {
      try {
        await apiPost(`/api/slots/${slot}/commands/${action}`)
        await this.refresh()
      } catch (err: any) {
        this.error = err?.message || String(err)
      }
    },
    async saveConfig(config: RuntimeConfig) {
      this.busy = true
      try {
        const res = await apiPut<{ ok: boolean; config: RuntimeConfig }>('/api/config', config)
        this.config = res.config
        await this.refresh()
        this.error = ''
      } catch (err: any) {
        this.error = err?.message || String(err)
        throw err
      } finally {
        this.busy = false
      }
    }
  }
})
