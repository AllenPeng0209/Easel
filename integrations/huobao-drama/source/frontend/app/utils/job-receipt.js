/** Receipt and completion are separate, and every observer follows the same job. */
export function jobReceipt(data, job, wait) {
  let completion
  return { data, job,
    completion() {
      if (!completion) completion = job ? Promise.resolve().then(() => wait(job)) : Promise.resolve()
      return completion
    },
  }
}
