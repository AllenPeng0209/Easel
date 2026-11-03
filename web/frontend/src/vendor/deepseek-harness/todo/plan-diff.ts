// Adapted from DeepSeek Harness @ ce7073d, ui-tool/tool/models/todo-diff-model.ts (MIT).
export interface PlanStep {
  key: string;
  content: string;
  status: string;
}

export function planDiff(previous: PlanStep[], current: PlanStep[]) {
  const previousByKey = new Map(previous.map(item => [item.key, item]));
  const currentKeys = new Set(current.map(item => item.key));
  const retainedPositions = new Map(previous.filter(item => currentKeys.has(item.key)).map((item, index) => [item.key, index]));
  let retainedIndex = 0;
  const changes: { item: PlanStep; labels: string[] }[] = [];
  for (const item of current) {
    const before = previousByKey.get(item.key);
    previousByKey.delete(item.key);
    const labels: string[] = [];
    if (!before) {
      labels.push('新增');
    } else {
      if (retainedPositions.get(item.key) !== retainedIndex++) labels.push('顺序调整');
      if (before.status !== item.status) labels.push('状态变更');
    }
    if (labels.length) changes.push({ item, labels });
  }
  for (const item of previousByKey.values()) changes.push({ item, labels: ['移除'] });
  return changes;
}
