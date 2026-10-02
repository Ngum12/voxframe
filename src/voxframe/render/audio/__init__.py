"""Audio mixing: the music bed and its ducking."""

from voxframe.render.audio.music import (
    MusicSettings,
    directed_mix_chain,
    music_filter_chain,
    simple_bed_chain,
)

__all__ = ["MusicSettings", "directed_mix_chain", "music_filter_chain", "simple_bed_chain"]
