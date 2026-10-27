# LUTs

Drop the HDR to SDR 3D LUT (.cube) in this folder. The engine picks up the
first LUT file here for every SDR conversion: proxy, share MP4s, thumbnails
and stills.

LUT files are ignored by git. Set `REELQUICK_SDR_LUT` to use one from
somewhere else, and `REELQUICK_SDR_LUT_RANGE` to `narrow` (default) or `full`
to match the range convention the LUT was built for. Run
`uv run reelquick lut-check` to measure which convention is right.
