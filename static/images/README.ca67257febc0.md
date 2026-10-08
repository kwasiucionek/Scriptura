# Header logo

`logo.png` is a compact PNG derived solely from the user's supplied
`staticfiles/logo.png`. No artwork or text was generated or added.
The original 2000 × 2000 file is unchanged.

For the header, empty outer margins were cropped to `(210, 206, 1797, 1654)`
and the image was resized to 320 × 292 with Pillow's LANCZOS filter, then
saved as an optimized RGB PNG (68,208 bytes). The logo retains its original
paper background, colours, book, light motif, and wordmark.

The template references this **source** asset through Django's `static` tag.
`staticfiles/` is generated deployment output, not a source directory;
keeping the header asset here prevents `collectstatic` from losing it.
