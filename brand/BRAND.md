# Recast logo

**Shift**: a file caught mid-recast, square on one side of the cut, round on the other. The square half is the file as it is; the blue round half is what it becomes.

## Files

| File | Use |
| --- | --- |
| `recast-mark.svg` | The mark on light backgrounds (ink + blue) |
| `recast-mark-reversed.svg` | On dark backgrounds (light ink + brighter blue) |
| `recast-mark-small*.svg` | 16–24 px: wider cut and rounder corners so the gap stays open |
| `recast-mark-black.svg`, `-white.svg` | One-colour printing, engraving, stamps |
| `recast-app-icon.svg`, `-small.svg` | App, installer and Start menu icon: the reversed mark on a dark tile |
| `recast.ico` | Windows icon, 16–256 px; each size drawn from the right master |

All of them are generated from `recast/ui/logo.py` by `brand/build_brand.py`, so the app's tray icon and these files can't drift apart.

## Colour

| Name | HEX | Role |
| --- | --- | --- |
| Ink | #14161B | Square half on light backgrounds; app tile |
| Light ink | #F3F4F6 | Square half on dark backgrounds |
| Recast blue | #2F6BFF | Round half on light backgrounds; also the Convert wheel |
| Recast blue, dark | #4C82FF | Round half on dark backgrounds |

Keep the two halves in different values: that's what stops the mark reading as a letter D.

## Use

- **Clear space:** the width of the cut ×2 on every side.
- **Minimum size:** 16 px, using the small cut below 32 px.
- **Tray icon:** the bare mark, matched to the taskbar's light or dark setting (the app switches automatically).
- **Don't:**
  - rotate it, or cut it diagonally (a slash across a shape reads as "no");
  - recolour the halves;
  - close the gap;
  - put the bare mark on mid-grey, where neither half has contrast. Use the app-icon tile there instead.

Trademark clearance hasn't been checked; search the trademark databases before using the name and mark publicly.
