import { createHash } from 'node:crypto'

export interface StoryboardVideoPromptContext {
  storyboardId: number
  storyboardUpdatedAt?: string
  description?: string | null
  savedVideoPrompt?: string | null
  episodeScript?: string | null
  duration?: number
  characters: Array<{ id: number; name: string; description?: string | null; appearance?: string | null; styling?: string | null }>
  siblingDescriptions?: Array<{ id: number; description: string | null }>
}

interface Window { start: number; end: number }
interface Definition { id: string; text: string; speakerKey?: string; speaker?: string; plannedWindow?: Window }
export interface CompiledDialogue extends Definition {
  speaker: string
  speakerSource: 'episode_script' | 'description_binding_name'
  matchedReferenceIds: number[]
  alreadyInPrompt: boolean
  localWindow?: Window
}
export interface CompiledStoryboardVideoPrompt {
  prompt: string
  provenance: {
    version: 1
    storyboardId: number
    storyboardUpdatedAt?: string
    originalPrompt: string
    savedVideoPrompt: string
    description: string
    episodeScriptSha256: string
    dialogue: CompiledDialogue[]
    characters: StoryboardVideoPromptContext['characters']
    removedWorkflowNotes: string[]
  }
}

const idPattern = '\\b(?:[a-z0-9]+[-_])*u\\d+\\b'
const idsIn = (text: string) => [...text.matchAll(new RegExp(idPattern, 'gi'))].map(m => m[0].toLowerCase())
const dialogueLine = /^\s*(?:对白|台词|原声)\s*[:：]/u
const genericReference = /(?:绑定|对应|按|依照)[^。\n]{0,40}U(?:句号|句|编号)/iu
const canonicalText = (text: string) => text.normalize('NFKC').replace(/\s+/gu, '')

function timeWindow(text: string): Window | undefined {
  const match = text.match(/(\d+(?:\.\d+)?)\s*[–—~～-]\s*(\d+(?:\.\d+)?)\s*(?:秒|s\b)/iu)
  if (!match) return undefined
  const start = Number(match[1]), end = Number(match[2])
  return start >= 0 && end > start ? { start, end } : undefined
}

function spokenText(text: string): string {
  let result = text.trim().replace(/^[：:]\s*/u, '')
  // These are planning annotations after a quotation, not words to speak.
  result = result.replace(/\s*[（(][^（）()\n]*(?:\d\s*(?:秒|s\b)|\d[^（）()\n]*[–—-]\s*\d|完整句|跨镜|同一句|不重说)[^（）()\n]*[）)]\s*$/iu, '').trim()
  if ((result.startsWith('“') && result.endsWith('”')) || (result.startsWith('「') && result.endsWith('」')) ||
      (result.startsWith('"') && result.endsWith('"'))) result = result.slice(1, -1).trim()
  return result
}

function descriptionDefinitions(description: string): Definition[] {
  const result: Definition[] = []
  for (const line of description.split(/\r?\n/u)) {
    if (!dialogueLine.test(line)) continue
    const body = line.replace(dialogueLine, '').trim()
    const match = body.match(new RegExp(`^(.*?)(${idPattern})\\s*[:：]\\s*(.+)$`, 'iu'))
    if (!match) continue
    const speakerKey = match[1].replace(/[｜|\s]+$/u, '').trim()
    const text = spokenText(match[3])
    if (!text || idsIn(text).length || genericReference.test(text)) continue
    result.push({ id: match[2].toLowerCase(), speakerKey: speakerKey || undefined, text,
                  plannedWindow: timeWindow(match[3]) })
  }
  return result
}

function speakerLabel(value: string): string {
  return value.trim().replace(/^[-*•]\s*/u, '').replace(/\s*[（(][^）)]*[）)]\s*$/u, '').trim()
}

function scriptDefinitions(script: string): Definition[] {
  const lines = script.split(/\r?\n/u)
  const result: Definition[] = []
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim()
    const matches = [...line.matchAll(new RegExp(idPattern, 'gi'))]
    if (matches.length !== 1) continue
    const match = matches[0], id = match[0].toLowerCase()
    const before = line.slice(0, match.index), after = line.slice((match.index ?? 0) + match[0].length)
    // Readable screenplay: - Speaker episode-u02 : “literal words” (timing).
    const quoted = after.match(/^\s*(?:[（(][^）)]*[）)]\s*)?[:：]\s*(.+)$/u)
    if (quoted && before.trim() && !before.includes('|') && !before.includes('｜')) {
      const text = spokenText(quoted[1])
      if (text && !idsIn(text).length) result.push({ id, text, speaker: speakerLabel(before), plannedWindow: timeWindow(quoted[1]) })
      continue
    }
    const cells = line.split(/[｜|]/u).map(cell => cell.trim()).filter(Boolean)
    if (cells[0]?.toLowerCase() !== id) continue
    if (timeWindow(cells[1] || '') && cells[2]) {
      // Full dialogue ledger: id | global time | speaker, followed by words.
      const next = lines.slice(index + 1).find(value => value.trim())?.trim() || ''
      if (next && !idsIn(next).length && !/^[#|｜【]/u.test(next)) {
        const text = spokenText(next.replace(/^\s*(?:对白|台词)\s*[:：]\s*/u, ''))
        if (text) result.push({ id, text, speaker: speakerLabel(cells[2]), plannedWindow: timeWindow(cells[1]) })
      }
    } else if (cells.length >= 3 && timeWindow((cells[2] || '') + '秒')) {
      // A repeated budget-table row supplies words, never a guessed speaker.
      const text = spokenText(cells[1])
      if (text && !idsIn(text).length) result.push({ id, text, plannedWindow: timeWindow(cells[2] + '秒') })
    }
  }
  return result
}

function matchingId(requested: string, definitions: Definition[]): string {
  if (/^u\d+$/iu.test(requested)) {
    const suffix = Number(requested.slice(1))
    const matches = [...new Set(definitions.filter(row => !/^u\d+$/iu.test(row.id) && Number(row.id.match(/u(\d+)$/iu)?.[1]) === suffix).map(row => row.id))]
    if (matches.length === 1) return matches[0]
    if (matches.length > 1) throw new Error(`对白 ${requested} 对应多个剧本编号，无法唯一解析`)
  }
  if (definitions.some(row => row.id === requested)) return requested
  throw new Error(`对白 ${requested} 无法唯一解析；请写明本镜逐字台词和说话角色，尚未提交生成`)
}

interface InputStatement { id?: string; speaker?: string; text: string }
const quotePattern = '(?:“([^”\\n]+)”|「([^」\\n]+)」|"([^"\\n]+)")'
const deliveryPattern = '(?:轻声|低声|小声|大声|柔声|平静地?|自然地?|温柔地?|笑着|哭着|轻轻地?){0,2}'
const speechVerbPattern = '(?:说道|说出|问道|回答|回应|喊道|说|问|答|喊)'
const escapePattern = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&')

function declarationContext(prompt: string, index: number): boolean {
  const before = prompt.slice(0, index)
  if (/(?:不要|不得|不能|禁止|勿|不是|并非)(?:让|由)?\s*$/u.test(before)) return false
  const clause = before.slice(Math.max(before.lastIndexOf('\n'), before.lastIndexOf('。'), before.lastIndexOf('；'), before.lastIndexOf(';')) + 1)
  return !/^\s*(?:画面文字|屏幕文字|字幕|标题|招牌文字)\s*[:：]/u.test(clause)
}

/** Only explicit U definitions and named quoted speech can contradict a line. */
function inputStatements(prompt: string, speakerNames: Set<string>): InputStatement[] {
  const names = [...speakerNames].filter(Boolean).sort((a, b) => b.length - a.length)
  const result: InputStatement[] = []
  for (const line of prompt.split(/\r?\n/u)) {
    const match = line.match(new RegExp(`(${idPattern})\\s*[:：]\\s*(.+)$`, 'iu'))
    if (!match || !declarationContext(line, match.index || 0)) continue
    const quoted = match[2].match(new RegExp('^' + quotePattern, 'u'))
    const text = quoted ? quoted[1] || quoted[2] || quoted[3] : spokenText(match[2])
    if (!text || idsIn(text).length) continue
    const prefix = speakerLabel(line.slice(0, match.index)).replace(dialogueLine, '')
      .replace(/[｜|\s]+$/u, '').replace(/^(?:@图片\d+|@|#)/u, '').trim()
    const speaker = names.find(name => prefix === name ||
      new RegExp(`^${escapePattern(name)}\\s*${deliveryPattern}${speechVerbPattern}$`, 'u').test(prefix))
    result.push({ id: match[1].toLowerCase(), speaker, text })
  }
  if (names.length) {
    const namedQuote = new RegExp(`(?<![\\p{L}\\p{N}_])(?:@图片\\d+|@|#)?(${names.map(escapePattern).join('|')})\\s*` +
      `(?:${deliveryPattern}${speechVerbPattern}\\s*[:：]?|[:：])\\s*${quotePattern}`, 'gu')
    for (const match of prompt.matchAll(namedQuote)) {
      if (declarationContext(prompt, match.index || 0)) {
        result.push({ speaker: match[1], text: match[2] || match[3] || match[4] })
      }
    }
  }
  return result
}

function removeWorkflowNotes(value: string, removed: string[]): string {
  return value
    .replace(/^\s*【(?:[A-Z]阶段|当前\s*[A-Z]\d+)[^】\n]*(?:批准|审查|验收|拍摄|交接)[^】\n]*】\s*/gmu, note => { removed.push(note.trim()); return '' })
    .replace(/此为[A-Z]阶段[^。\n]*(?:未提交生成|尚未拍摄)[。]?/gu, note => { removed.push(note.trim()); return '' })
    .replace(/^\s*[A-Z]\d+待用户[A-Z]?验收[｜|]\s*\d+(?:\.\d+)?\s*[–—~-]\s*\d+(?:\.\d+)?秒[｜|](.*)$/gmu,
      (note, visual: string) => { removed.push(note.slice(0, note.length - visual.length)); return visual })
    .replace(/^当前绑定[^\n]*(?:档案文档|未制作)[^\n]*$/gmu, note => { removed.push(note.trim()); return '' })
    .trim()
}

/** Compile only explicit same-storyboard evidence. Never infer missing dialogue. */
export function compileStoryboardVideoPrompt(inputPrompt: string, context: StoryboardVideoPromptContext): CompiledStoryboardVideoPrompt {
  const description = context.description || '', script = context.episodeScript || ''
  const rawDescriptions = descriptionDefinitions(description), rawScripted = scriptDefinitions(script)
  const rawDefinitions = [...rawDescriptions, ...rawScripted]
  const fullId = (row: Definition): Definition => /^u\d+$/iu.test(row.id)
    ? { ...row, id: matchingId(row.id, rawDefinitions) } : row
  const descriptions = rawDescriptions.map(fullId), scripted = rawScripted.map(fullId)
  const definitions = [...descriptions, ...scripted]
  const requested = [...new Set([...descriptions.map(row => row.id), ...idsIn(inputPrompt),
    ...description.split(/\r?\n/u).filter(line => dialogueLine.test(line)).flatMap(idsIn)])]
  if (!requested.length && genericReference.test(inputPrompt + '\n' + description)) {
    throw new Error('本镜U句未提供可解析的逐字对白；请先补全台词和说话角色，尚未提交生成')
  }
  const selected = [...new Set(requested.map(id => matchingId(id, definitions)))]
  const shotWindow = description.split(/\r?\n/u).filter(line => !dialogueLine.test(line))
    .map(line => /[｜|]/u.test(line) ? timeWindow(line) : undefined).find(Boolean)
  const characters = context.characters.map(row => ({ ...row }))
  const names = new Set(characters.map(row => row.name))
  const statements = inputStatements(inputPrompt, new Set([...names,
    ...definitions.flatMap(row => [row.speaker, row.speakerKey]).filter((name): name is string => !!name)]))
  const dialogue: CompiledDialogue[] = selected.map(id => {
    const shared = (context.siblingDescriptions || []).filter(row => row.id !== context.storyboardId &&
      (row.description || '').split(/\r?\n/u).filter(line => dialogueLine.test(line)).some(line =>
        idsIn(line).some(other => other === id || (/^u\d+$/iu.test(other) || /^u\d+$/iu.test(id)) &&
          Number(other.match(/u(\d+)$/iu)?.[1]) === Number(id.match(/u(\d+)$/iu)?.[1]))))
    if (shared.length) throw new Error(`对白 ${id} 跨分镜共享；请合并生产单元或明确整句仅生成一次的归属，不能独立重复合成`)
    const rows = definitions.filter(row => row.id === id)
    if (new Set(rows.map(row => canonicalText(row.text))).size !== 1) {
      throw new Error(`对白 ${id} 的逐字文本存在冲突，请核对剧本与本镜描述后再生成`)
    }
    const own = descriptions.find(row => row.id === id), source = own || rows[0]
    const speakers = [...new Set(rows.flatMap(row => [row.speaker, names.has(row.speakerKey || '') ? row.speakerKey : undefined]).filter((name): name is string => !!name))]
    if (speakers.length > 1) throw new Error(`对白 ${id} 的说话角色存在冲突，请先核对角色映射`)
    if (speakers.length !== 1 || (!names.has(speakers[0]) && /^[a-z0-9]+(?:[-_][a-z0-9]+)+$/iu.test(speakers[0]))) {
      throw new Error(`对白 ${id} 的说话角色 ${source.speakerKey || speakers[0] || ''} 无法对应当前绑定人物，不能猜测发言者`)
    }
    for (const statement of statements) {
      const sameWords = canonicalText(statement.text) === canonicalText(source.text)
      if (statement.id ? matchingId(statement.id, definitions) !== id : !sameWords) continue
      if (statement.id && !sameWords) throw new Error(`对白 ${id} 的输入台词文本与本镜剧本存在冲突，请先核对后再生成`)
      if (statement.speaker && statement.speaker !== speakers[0] && statement.speaker !== own?.speakerKey) {
        // Identical short replies can legitimately belong to separate U lines.
        // A named quote matching another selected line is not this line's cast.
        if (!statement.id && definitions.some(row => selected.includes(row.id) &&
            canonicalText(row.text) === canonicalText(statement.text) &&
            (row.speaker === statement.speaker || row.speakerKey === statement.speaker))) continue
        throw new Error(`对白 ${id} 的输入说话角色与本镜剧本存在冲突，请先核对后再生成`)
      }
    }
    const plannedWindow = own?.plannedWindow || rows.find(row => row.plannedWindow)?.plannedWindow
    let localWindow: Window | undefined
    if (plannedWindow && shotWindow) {
      localWindow = { start: Number((plannedWindow.start - shotWindow.start).toFixed(6)),
                      end: Number((plannedWindow.end - shotWindow.start).toFixed(6)) }
      if (localWindow.start < 0 || localWindow.end <= localWindow.start ||
          context.duration !== undefined && localWindow.end > context.duration) {
        throw new Error(`对白 ${id} 的时间窗超出本片段时长，请明确一次生成的完整语句范围`)
      }
    }
    const hasText = source.text.length >= 4 ? canonicalText(inputPrompt).includes(canonicalText(source.text))
      : ['「', '“', '"'].some(quote => inputPrompt.includes(quote + source.text))
    const attributed = statements.filter(row => canonicalText(row.text) === canonicalText(source.text) && (row.id || row.speaker))
    const alreadyInPrompt = hasText && (!attributed.length || attributed.some(row => row.id
      ? matchingId(row.id, definitions) === id : row.speaker === speakers[0] || row.speaker === own?.speakerKey))
    return { id, speakerKey: own?.speakerKey, speaker: speakers[0],
             speakerSource: rows.some(row => row.speaker === speakers[0]) ? 'episode_script' : 'description_binding_name',
             matchedReferenceIds: characters.filter(row => row.name === speakers[0]).map(row => row.id), text: source.text,
             plannedWindow, localWindow, alreadyInPrompt }
  })
  const removedWorkflowNotes: string[] = []
  const provenance: CompiledStoryboardVideoPrompt['provenance'] = {
    version: 1, storyboardId: context.storyboardId, storyboardUpdatedAt: context.storyboardUpdatedAt,
    originalPrompt: inputPrompt, savedVideoPrompt: context.savedVideoPrompt || '', description,
    episodeScriptSha256: createHash('sha256').update(script).digest('hex'), dialogue, characters, removedWorkflowNotes,
  }
  if (!description.trim() && !dialogue.length) return { prompt: inputPrompt, provenance }
  const original = removeWorkflowNotes(inputPrompt, removedWorkflowNotes)
  const visual = removeWorkflowNotes(description.split(/\r?\n/u)
    .filter(line => !(dialogueLine.test(line) && idsIn(line).length)).join('\n'), removedWorkflowNotes)
  const sections = [original]
  if (visual) sections.push(`【本镜动作与连续性】\n${visual}`)
  if (characters.length) sections.push('【绑定参考说明】\n以下是素材标签，不代表新增演员；出场与发言角色仅以本镜动作、剧本及逐字对白为准。\n' + characters.map(row =>
    `${row.name}：${[row.description, row.appearance, row.styling].filter(Boolean).join('；')}`).join('\n'))
  if (dialogue.length) sections.push('【逐字对白与说话者】\n编号仅作角色与台词映射，不朗读编号；每句完整说一次，不添加或重复对白。时间从本片段0秒开始。\n' + dialogue.map(row => {
    const identity = [row.speakerKey, row.id, row.localWindow ? `本片段 ${row.localWindow.start}–${row.localWindow.end}秒` : undefined].filter(Boolean).join('；')
    return `${row.speaker}（${identity}）：${row.alreadyInPrompt ? '完整台词已在上方提示词中给出，仅说一次。' : `<d>[Chinese] ${row.text}</d>`}`
  }).join('\n'))
  return { prompt: sections.filter(Boolean).join('\n\n'), provenance }
}
