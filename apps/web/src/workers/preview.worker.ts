/// <reference lib="webworker" />
import { previewFile, type WarehouseBundle } from '@/lib/browserPreview'

let warehouse: Promise<WarehouseBundle> | null = null

self.onmessage = async (event: MessageEvent<{ name: string; data: ArrayBuffer; hint?: string; warehouseUrl: string }>) => {
  const { name, data, hint, warehouseUrl } = event.data
  try {
    warehouse ??= fetch(warehouseUrl).then((r) => {
      if (!r.ok) throw new Error(`Demo warehouse could not be loaded (${r.status})`)
      return r.json()
    })
    const result = await previewFile({ name, data }, await warehouse, hint)
    self.postMessage({ ok: true, result })
  } catch (error) {
    self.postMessage({ ok: false, error: (error as Error).message })
  }
}
