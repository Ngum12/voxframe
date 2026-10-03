/**
 * Stands in for `rvfc-polyfill`, which JASSUB imports for browsers without
 * `requestVideoFrameCallback` (D-196).
 *
 * Every browser Voxframe supports has it natively, and the polyfill is
 * GPL-3.0, which this Apache-2.0 app does not ship. So nothing is imported.
 */
export {};
