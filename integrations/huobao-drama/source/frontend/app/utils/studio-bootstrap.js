/** Independent episode modules must not add their network round trips together. */
export async function loadEpisodeAssets(api, episodeId) {
  const [characters, scenes, props, storyboards] = await Promise.all([
    Promise.resolve().then(() => api.characters(episodeId)).catch(() => []),
    Promise.resolve().then(() => api.scenes(episodeId)).catch(() => []),
    Promise.resolve().then(() => api.props(episodeId)).catch(() => []),
    Promise.resolve().then(() => api.storyboards(episodeId)),
  ])
  return { characters, scenes, props, storyboards }
}
