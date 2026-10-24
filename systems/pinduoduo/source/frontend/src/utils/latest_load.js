// List requests share one generation; stale responses never update another selection.
export function createLatestLoad(setBusy) {
  let generation = 0
  return {
    invalidate() { ++generation; setBusy(false) },
    async run(fetchData, applyData) {
      const current = ++generation
      setBusy(true)
      try {
        const data = await fetchData()
        if (current !== generation) return false
        applyData(data)
        return true
      } catch (error) {
        if (current === generation) throw error
        return false
      } finally {
        if (current === generation) setBusy(false)
      }
    },
  }
}
