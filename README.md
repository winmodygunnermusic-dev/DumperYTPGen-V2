# DumperYTPGen

DumperYTPGen — A classic YouTube Poop (YTP) auto-generator built with Python + FFmpeg and a Tkinter GUI.

This project provides an offline, pure-Python desktop app that:
- Automatically generates short random clips from source videos.
- Applies YTP-style visual/audio effects using FFmpeg filter graphs.
- Combines clips, overlays images/videos, adds SFX and music, and exports final MP4s.
- Is designed for Windows 8.1 / 10 / 11 (also works on other platforms with FFmpeg installed).

Important: This project uses FFmpeg and FFprobe via subprocess. It does not use MoviePy, NumPy, or OpenCV.

Requirements
- Python 3.8 or newer (3.8+)
- FFmpeg and FFprobe installed and available on PATH, or set via the app configuration
- Windows 8.1 / 10 / 11 recommended (app uses os.startfile for previews on Windows)

No third-party Python packages are required.

Quick start (run locally)
1. Ensure FFmpeg / FFprobe are installed and accessible from the command-line.
   - Download from https://ffmpeg.org/ and add the bin folder to PATH, or place ffmpeg.exe and ffprobe.exe somewhere and configure their paths from the app (Config → Set FFmpeg Path / Set FFprobe Path).
2. Place the project files in a directory (the repository files include: `gui.py`, `ffmpeg_utils.py`, `clip_generator.py`, `effect_engine.py`, `library_manager.py`, `project_manager.py`, `export_manager.py`, `config.py`, `__main__.py`).
3. Run the GUI:
   - From the project directory: `python gui.py`
   - Or (if running as module) `python -m __main__` (or package-launch after installation).

High-level features
- Source Library Manager: import/remove/manage video, audio, images, sound effects, dance music and transition clip libraries. Libraries are saved in JSON config.
- Auto Clip Generator: scan source videos, detect durations with FFprobe, create randomized trimmed clips to temporary folder.
- Auto YTP Generator: combine generated clips with random reorder, jump cuts, repeats, reverse, speed changes, freeze frames, audio pitch/volume effects and stutters.
- Overlay System: random image/video overlays, adjustable duration, positions (top-left/top-right/center/bottom-left/bottom-right), multiple overlays per project.
- Sound Effect System: soundboard-style SFX insertion, random timing, layering over clips.
- Dance Music Mode: select music track, auto-cut clips to beat intervals, quick montage generation, music volume control.
- Transition System: hard cuts, fades, fade to black, flash, basic zoom transition using FFmpeg filters.
- Old School YTP Effects: ear-rape, repeat word, reverse sentence, zoom, spin, mirror, RGB-split simulation, random subtitles, freeze frame, stutter, shuffle edits, meme inserts (many implemented as FFmpeg filter-based approximations).
- Project System: save/load project JSON containing clips, overlays, and settings. Recent projects list persisted.
- Export System: MP4 H.264 output with configurable bitrate, progress and cancel support, real-time log window.
- UI: Tkinter + ttk notebook tabs: Sources, Auto Clips, Effects, Audio, Overlays, Export. Statusbar + progress.
- Technical: modular code (see Modules section), object-oriented, threaded long-running ops, config/presets, temporary cache manager, batch generation scaffolding.

Project modules (files)
- config.py — configuration manager (JSON-based, stores library paths, presets, recent projects).
- ffmpeg_utils.py — wrapper for running ffmpeg/ffprobe, probing files, building trims, applying filters, parsing progress.
- library_manager.py — add/remove/list/preview library files.
- clip_generator.py — random clip generator and temporary cache manager.
- effect_engine.py — implements many YTP effects via FFmpeg filters (speed, reverse, freeze, zoom, spin, mirror, rgb-sim, subtitles, ear-rape, stutter, etc).
- project_manager.py — save & load project JSON files and maintain recent projects list.
- export_manager.py — assembles project, applies overlays and mixes audio, runs export through FFmpeg with progress/cancel support.
- gui.py — Tkinter GUI, application entry for interactive usage.
- __main__.py — convenience entry-point for module launching.

Configuration and data locations
- The app stores configuration in a JSON file in the user config directory:
  - Windows: %APPDATA%\DumperYTPGen\dumperytpgen_config.json
  - Other OS: ~/.dumperytpgen/dumperytpgen_config.json
- Default temporary files are stored in the config directory under `temp/`. You can clear temp files via the Sources tab.

Using the app (basic workflow)
1. Open Sources tab
   - Import your video files to the Videos library.
   - Import images (overlays), SFX, dance music, transitions and audio library entries as desired.
2. Auto Clips tab
   - Set minimum and maximum clip lengths and the number of clips to generate.
   - Click "Generate Clips". Generated clips are exported to the configured temp folder and listed for preview/editing.
3. Effects tab
   - Select one or more generated clips, pick effects (speed, reverse, freeze, ear-rape, subtitles, etc.), and apply to individual clips or randomly to all.
   - Shuffle clips if you want randomized ordering.
4. Overlays tab
   - Choose images or short videos and add them as overlays with a duration and position.
5. Audio tab
   - Use Dance Music Mode to auto-cut clips to beat intervals and quickly create montages.
6. Export tab
   - Choose your output path and bitrate and click "Export YTP".
   - Watch the real-time FFmpeg log and progress bar.
   - Use Cancel Export to request termination (best-effort).

Project files
- Projects are saved as JSON with structure similar to:
```json
{
  "clips": [
    "C:\\...\\temp\\clip_abc123.mp4",
    "C:\\...\\temp\\clip_def456.mp4"
  ],
  "overlays": [
    {"path": "C:\\path\\to\\overlay.png", "duration": 1.0, "position": "top-left"}
  ],
  "settings": {
    "bitrate": "2000k"
  }
}
