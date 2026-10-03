/**
 * Stands in for `lfa-ponyfill`, which JASSUB's worker uses to look fonts up on
 * Google Fonts (D-196).
 *
 * Voxframe works offline and turns that lookup off (`queryFonts: false`): the
 * captions use only the fonts bundled with the app, the same ones the video
 * is rendered with. Its font list is also rewritten from the network when it
 * is installed, which would make the build differ from one day to the next.
 */
export async function queryRemoteFonts(): Promise<never[]> {
  return [];
}

export default async function queryLocalFonts(): Promise<never[]> {
  return [];
}
