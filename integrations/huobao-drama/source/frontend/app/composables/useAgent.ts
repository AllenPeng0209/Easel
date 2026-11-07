import { toast } from 'vue-sonner'
import { api } from './useApi'
import { toastError } from './useToast'
import { i18n } from './i18n'

export function useAgent() {
  const running = ref(false)
  const runningType = ref<string | null>(null)

  async function run(type: string, msg: string, dramaId: number, episodeId: number, onDone?: () => void, model?: string, configId?: number) {
    if (running.value) { toast.warning(i18n.global.t('composables.agent.busy')); return }
    running.value = true
    runningType.value = type
    let tracking = false
    try {
      const receipt = await api.start<any>(`/agent/${type}/chat`, {
        message: msg,
        drama_id: dramaId,
        episode_id: episodeId,
        model: model || undefined,
        config_id: configId || undefined,
      })
      if (receipt.job) {
        tracking = true
        toast.info('任务已接收，可在后台任务中查看进度')
        void receipt.completion().then(() => {
          toast.success(i18n.global.t('composables.agent.done'))
          return onDone?.()
        }).catch(error => toastError(error)).finally(() => {
          running.value = false; runningType.value = null
        })
        return receipt.job.id
      }
      toast.success(i18n.global.t('composables.agent.done'))
      onDone?.()
    } catch (err: any) {
      toastError(err)
    } finally {
      if (!tracking) { running.value = false; runningType.value = null }
    }
  }

  return { running, runningType, run }
}
