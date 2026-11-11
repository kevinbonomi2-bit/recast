# Using Recast

Recast lives in the tray. Click the icon for Settings; right-click it for the menu.

## The wheel

1. Start dragging a file (or several) in File Explorer or on the desktop.
2. Tap **Shift**, **Ctrl** or **Alt**. The wheel opens under your pointer.
3. Drop on a tile.

| Key | Wheel | What's on it |
| --- | --- | --- |
| Shift | Convert | Other formats for this kind of file |
| Ctrl | Tools | Compress, crop, trim, rotate, merge, strip metadata… |
| Alt | Actions | Copy, copy path, move to…, terminal here, show in folder, ZIP |

- Tap another key while the wheel is open to switch wheels.
- **Esc**, or dropping outside the wheel, cancels.
- The pill above the wheel spells out the result before you drop ("holiday.webp ≈ 38 KB · 43% smaller").
- After the drop the wheel folds onto your file and shows progress. Longer jobs continue on a card above the taskbar; right-click the card to cancel.
- Results are saved next to the original (`photo.webp`, `photo-compressed.jpg`) and never overwrite anything.

### Repeat, undo, favourites

- **Repeat:** the wheel opens with your pointer on its centre. If you've converted this kind of file before, your last pick lights up; let go to repeat it. Turn this off in Settings to make the centre cancel instead.
- **Undo:** after a conversion, click **Undo** under the centre (or on the card). The new file goes to the Recycle Bin. The original is never touched.
- **Favourites:** tap the wheel's key again over a tile (or right-click it) to pin it. Pinned tiles get a dot and lead the wheel from the next time it opens.

### Without dragging

- **Clipboard:** copy a file or an image, then press **Ctrl+Alt+R**. A copied image is saved to `Pictures\Recast` first.
- **Send to:** right-click files → Send to → Recast (turn it on in Settings).
- In both cases click a tile, or use the arrow keys and Enter.

## Tools that open a window

- **Crop…**: drag the frame, pick an aspect ratio or type exact pixels. A selection of images shares one framing.
- **Trim…** (audio and video): drag the start and end handles. Space plays the part you keep; I and O set the ends at the playhead.
- **Compress to size…**: aim for a size ("2 MB"), a reduction ("60% smaller") or a preset.

## For Discord

On the Tools wheel for any video. It shrinks the clip to just under your Discord upload limit and copies it, so you can paste it straight into Discord with Ctrl+V. Clips that already fit are copied as they are. Set your limit in Settings: 20 MB (free), 50 MB (Nitro Basic) or 1 GB (Nitro).

## Clip Inbox

When Xbox Game Bar, AMD ReLive, NVIDIA, OBS or Discord saves a game clip, a card appears above the taskbar with four buttons: **Trim**, **For Discord**, **Keep** and **Delete**.

- Cards wait until you leave a full-screen game.
- **Keep** moves the clip into a folder named after the game. Ignoring a card keeps the clip too.
- **Delete** waits five seconds with an Undo, then sends the clip to the Recycle Bin.
- If clips older than 30 days add up to 2 GB, Recast offers a review. Nothing is ticked for you.

Game Bar's own gallery only lists clips left loose in `Captures`, so turn sorting off in Settings if you use that gallery.

## HDR screenshots

Xbox Game Bar saves HDR screenshots as `.jxr`, which almost nothing opens. Recast converts them to PNG, JPG, WEBP or AVIF with tone mapping, so highlights keep their colour instead of clipping to white. **Settings › HDR screenshot brightness** matches how bright the game looked.

## Settings

- **Keys:** which key opens which wheel. "Other…" records any key or a mouse side button.
- **Wheel:** light or dark, drags from any app, repeat on the centre, the clipboard hotkey.
- **Saving:** next to the original or in one folder, photo quality, keeping camera and location data, Compress strength, Discord limit, HDR brightness.
- **Clip Inbox:** on or off, sorting by game, an extra folder to watch, the old-clip reminder.
- **Windows:** start with Windows, the Send to entry, the log file.

## Formats

- **Images:** JPG, PNG, WEBP, HEIC, AVIF, TIFF, BMP, GIF, ICO, PDF. SVG and JXR as input. Animated GIF → MP4.
- **Audio:** MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA.
- **Video:** MP4, MOV, MKV, WebM, AVI, WMV, GIF, plus MP3 / M4A / WAV export. Container changes are remuxed without re-encoding when the codecs allow it.
- **PDF and e-books:** PDF → PNG / JPG (every page at 300 DPI), TXT, Markdown. EPUB / MOBI / FB2 → PDF, TXT, images.
- **Documents:** DOCX / Markdown / TXT / HTML → HTML, Markdown, TXT.
- **Spreadsheets:** CSV, TSV, XLSX (leading-zero IDs stay text).
- **Subtitles:** SRT, VTT, TXT.
- **Archives:** ZIP, TAR, TAR.GZ, and extract (7z and RAR with 7-Zip).

Optional helpers, found automatically when installed:

- **FFmpeg** for audio and video: `winget install Gyan.FFmpeg`
- **LibreOffice** for Word, PowerPoint and Excel to PDF: `winget install TheDocumentFoundation.LibreOffice`
- **7-Zip** for .7z and .rar: `winget install 7zip.7zip`

## Privacy

Recast makes no network connections. Files are converted on your PC and nothing is collected.

## Troubleshooting

- **The wheel doesn't open.** Check the tray icon is there, and that the drag started in File Explorer or on the desktop (or turn on "Work with drags from any app").
- **A conversion failed.** Settings › Log file shows why.
- **No audio or video options.** FFmpeg isn't installed; see above.
