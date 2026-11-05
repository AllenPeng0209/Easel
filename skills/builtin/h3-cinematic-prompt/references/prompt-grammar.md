# 视听提示词语法

以下字段、标签与时间码都编译进 Host 制作清单的 `prompt` 字符串，最终由适配器传给模型。它们是源文推荐的经验组织方式，不是 Host／服务端 JSON schema，也不保证逐 token 被强制执行。处理中文对白时，场景、动作、镜头与声音归属使用英文；中文原词只放在对白容器内。

## 基础三字段

```text
integrated_multimodal_description: [Shot 1] Live-action. An eye-level medium shot frames [subject] in [place]. [A clear action develops]. [Camera type, amplitude, speed and target]. [Synchronized sound or dialogue].
overall_soundscape: [Continuous ambience and spatial character, without duplicating dialogue or music].
non_diegetic_music: [Instruments, tempo and volume arc, or N/A].
```

画内对白、同步动作声写入时间轴；持续环境底噪写 soundscape；角色听不到的配乐写 non_diegetic_music。具体脚步或纸声需要同步时归入时间轴，不再在 soundscape 复述一次。无配乐写 `N/A` 是提示意图，不等于关闭生成音轨。

## 全参考六段式

当前 Ref2VA 优先使用此结构，顺序保持一致：

```text
subject_definitions: <Subject 1> is the adult woman on the left in Image 1, wearing the same beige cardigan; her voice is (S1). <Subject 2> is the boy on the right; his voice is (S2).
summary: The woman pauses, then the boy answers.
retention_analysis: Preserve both identities and clothing from Image 1. Use Audio 1 only as the timbre reference for (S1), and Audio 2 for (S2); do not repeat reference words.
detailed_description: [Shot 1] Live-action, a static eye-level medium two-shot. The woman (S1) says quietly: <d>[Chinese] 先停一下。</d> The boy listens with his lips closed. [Shot 2] At 00:02.500, the camera cuts to the boy (S2), who replies: <d>[Chinese] 好。</d> The woman remains silent.
overall_soundscape: Quiet indoor room tone and natural breathing.
non_diegetic_music: N/A.
```

上面只是通用的极短语法例，假设本段实际提供一张双人图和两段音频，不是本次影片剧本，也不授权新增参考。参考输入按 Host 实际绑定的各模态数组顺序分别从 1 编号，保留素材映射表。已部署 Ref11 只接收数组内的 `data_url`／文件 ID，不会把本地文件名传给模型，不能靠文件名绑定人物或声线。

`<Subject N>` 指复用的人、场景、道具或视觉内容；`<Picture N>` 可描述某张具体帧；`<Video N>` 描述某段视频的结构、动作或承接关系；`<Audio N>` 描述音色/声音参考。源文的 Subject/Picture 名称属于文字层，不能自行改变接口实际采用的 `Image N`、`Video N`、`Audio N` 映射。每段重新核对局部编号，未输入的标签不出现；例如前段的母亲是 `Image 2 / Video 2`，后段仅有她的图与视频时改为 `Image 1 / Video 1`，但母亲的声音身份仍保持 `(S1)`。

只保留参考素材中任务需要的属性，明确其他元素是否改变。例如借人物身份而不借原图字幕，借音色而不要求复述原音频。这样的文字不能确保模型消除硬字幕或完全分离音色，结果仍需审验。

## 镜头与切点

首镜 `[Shot 1]`，后续依次 `[Shot 2] At 00:03.500, the camera cuts to ...`。切点严格递增、落在请求时长内，并提供新信息。秒数是导演目标，需实片核对。

普通硬切使用 cuts to；叠化或淡入淡出按用户的剪辑意图添加。不要自行发明切镜 token。

## 说话人、对白与声音

- 发声主体分配稳定 ID，例如母亲 `(S1)`、孩子 `(S2)`。首次写清身份、是否入画、音高音色、语速和口音；跨镜与跨段不换 ID。视觉主体编号、声音 ID 与局部参考编号分开管理：场景／道具不因此取得声音 ID。ID 是提示约定，不能保证音色克隆。
- 对白原文、标点与语言不改。每句明确唯一说话人，听者不同时张口；容器内只放语言标签与原话，英文动作和 delivery 放在容器外。对白长于自然可用时长时，先改剧本或分段，不让模型抢读来凑时。

```text
The adult woman (S1), speaking Mandarin in a low, restrained voice, says: <d>[Chinese] 最后一班，也不是终点。</d>
```

- 内心旁白说明 off-screen voiceover，并写清嘴唇保持闭合，生成后检查是否真的无口型。

```text
The woman (S1) speaks in an off-screen voiceover: <d>[Chinese] 这一次，我自己走。</d> Her lips remain closed while she watches the rain.
```

来源技能记录的既往范例中，上述闭口旁白意图仍伴随可见开闭嘴，不能据提示文本验收为全程闭口 VO。若闭口是硬要求，必须逐段审片；可按获准的表现形式改用背影等镜头并保留原生音轨，或明确另存后期声画剪辑版，不能把修补结果冒充原生闭口已通过。需要画内对话时，不能用道具镜头遮掩已失败的口型。

- 克制可写 low and restrained，发颤可写 trembling with short catches of breath，温柔可写 soft with slight breathiness，坚定可写 steady and quiet。先有情绪动机再落可听的 delivery，不只写抽象情绪词。
- 跨镜声音可使用源文的 `<scenetrans>` 并说明 continues seamlessly across the cut；截断意图可用 `<cutoff>`。这些都是文字惯例；跨段自然接话容易断口，优先在台词说完后切段，必要时后期对轨并保留原轨。

## 正向约束

当前接口没有独立 negative_prompt。把要求写成可观察结果：自然皮肤纹理、物体接触阴影、稳定面部特征、单一连贯动作、自然休息的手。需要干净画面时可写 `a clean frame with no on-screen text or logos`，但不能保证去除参考图已有硬字幕。避免长串反面病症和相互冲突的要求。
