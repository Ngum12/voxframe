import type { BookendControls, BookendSignature } from "./api";

export function bookendDefaults(data: BookendControls, signature: BookendSignature) {
  const pairs = data.opening.endings.flatMap(a => data.closing.starts
    .filter(b => a.last_word < b.first_word && a.end <= b.start)
    .map(b => ({last_word: a.last_word, first_word: b.first_word,
      distance: Math.abs(a.last_word + 1 - signature.opening_words) + Math.abs(data.word_count - b.first_word - signature.closing_words)})));
  pairs.sort((a, b) => a.distance - b.distance);
  return pairs[0] ? {last_word: pairs[0].last_word, first_word: pairs[0].first_word} : null;
}
