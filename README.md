# Stradale Studio

A local Mac app that replaces plate text in car photos. Select a folder, set the text,
and process the images through a persistent queue. No photos are sent to a cloud service.

## Use the app

Open **Stradale Studio** from your Applications folder.

1. Choose the **Source folder**. Enable **Include subfolders** if needed.
2. Choose a separate **output folder**, outside the source folder.
3. Set **Find text** (for example `VEHIS`) and **Replace with** (`STRADALE`).
4. Select **Add folder to queue**, then **Start queue**.
5. Check **Need review** and **Failed**. Open saved images with **Inspect**.
6. Select **Save report** for a CSV containing paths, status, errors, and processing times.

The queue runs two images at a time. It can contain 10,000 images without loading them all
into memory. Pause stops new work; active images finish. Quit saves queue progress. The next
launch starts paused, and **Start queue** resumes waiting images. Keep the Mac awake and
leave the app open while it processes the queue. The app does not run as a background service
after you quit it.

Every batch has a new output folder. Subfolders are retained. Output names include the
source extension (`car.jpg.png`) to avoid collisions between `car.jpg` and `car.png`.
Existing files are never overwritten by the batch processor. Re-adding a folder creates a
new batch; it does not deduplicate against earlier batches.

### Single images and manual review

Use **Single image** to load one photo. The editor can find the plate or let you select four
corners: top left, top right, bottom right, bottom left. Select only the white plate face,
excluding the blue country strip. Drag corners to adjust them. Use **Preview replacement**,
compare with the original, then **Save PNG**.

A queue image marked **Need review** or **Failed** can be opened in the same editor.
After a valid preview, **Save to batch output** writes the repaired result and marks the
queue item as saved. A damaged or missing source file cannot be repaired in the editor.
For a source file that changes after import, add its folder again.

## Installation and build

The build is for **Apple Silicon Macs** and uses a macOS 14 deployment target. It was tested
on an M4 Pro with 48 GB RAM and macOS 26.6.2. Other Mac versions have not been tested.

The `.app` includes the Python runtime, OpenCV, Pillow, and a compiled Apple Vision helper.
Users do not need Python, uv, Terminal, or a model download. The bundle is signed locally
with an ad-hoc signature. It is not Developer ID signed or notarized for general distribution.

To build from source, install the Xcode command line tools and `uv`, then run:

```sh
scripts/build-app.sh
open 'dist/Stradale Studio.app'
```

Copy the app into `~/Applications` to install it for your account. The build script replaces
only generated output in `dist`. It does not update an installed copy automatically.

For development without the native window:

```sh
scripts/dev.sh --data-dir .test-data/dev
```

Open the session URL printed by the process. Folder paths can be entered directly in this
browser mode. Native folder dialogs, Finder controls, and save panels are available in the
Mac app.

## Processing and storage

- Apple Vision recognizes the requested text locally. OpenCV finds a light, four-sided
  plate face around that text and fits the replacement to it.
- One clear OCR text match is required. No match, multiple matches, or uncertain geometry
  sends the image to review. This is a focused detector for clear dealership photos, not a
  general plate recognition model.
- The replacement uses a fixed bold font and an estimate of the plate's light gradient.
  It does not reproduce arbitrary typefaces, glare, or reflections exactly. Check results.
- Supported files: PNG, JPEG, WebP, up to 24 million pixels and 60 MB per source file.
  Hidden files and symbolic links are skipped. Folder import is synchronous but does not
  decode images. It fails if a folder cannot be read.
- Output is RGB PNG. Orientation is corrected. Source metadata and color profiles are not
  copied. Pixels outside the selected plate polygon remain equal to the decoded image.
- SQLite stores batches and per-image state at
  `~/Library/Application Support/Stradale Studio/queue.sqlite3`.
- On interrupted runs, active jobs return to waiting. If an output already exists after
  a crash, that job goes to review instead of overwriting it. Temporary files have hidden
  unique names. Output publishing uses an atomic hard link and requires a filesystem that
  supports hard links, such as APFS.
- The local HTTP service binds to a random loopback port. A per-launch token protects the
  session. The native window only accepts navigation and app messages from that service.
- The app log is `~/Library/Application Support/Stradale Studio/app.log`.
- Source files must stay at their original paths until the batch is complete. The app checks
  file size and modification time before and after processing.

## Verification

```sh
uv run python -m unittest discover -s tests -v
```

The tests cover a 10,000-item import and queue recovery, two-worker limits, pause/resume,
failed-image isolation, filename collisions, source changes, manual repair, output overwrite
protection, pixels outside the plate, and local HTTP authentication.

On the development machine, the native app processed 30 copies of the supplied 693 × 523
photo, one blank photo, and one damaged file. The 30 car photos were saved, the blank photo
went to review, and the damaged file failed without blocking other jobs. A pause, quit,
relaunch, and resume test completed the remaining seven images. The original files and
pixels outside every selected plate were checked. Native folder selection and CSV export
were tested. Per-image processing for the small sample was approximately 0.24–0.34 seconds
with two workers.

The 10,000-item test checks queue storage and pagination, not image detection on 10,000
unique photos. A varied production photo set still needs validation. Large images will
have different processing time and memory use.

## Source layout

- `native/App.swift`: Mac window, folder/save dialogs, processor lifecycle.
- `native/recognize.swift`: Apple Vision OCR helper.
- `studio/engine.py`: plate detection and deterministic text rendering.
- `studio/queue_store.py`: persistent queue, workers, and safe output publishing.
- `studio/server.py`: authenticated local API.
- `studio/ui/`: queue and image editor.

Based on the first Plate Studio prototype. References:
[Apple Vision](https://developer.apple.com/documentation/vision/vnrecognizetextrequest),
[OpenCV perspective transforms](https://docs.opencv.org/4.4.0/da/d54/group__imgproc__transform.html),
and [PyInstaller packaging](https://pyinstaller.org/en/v6.17.0/usage.html).
