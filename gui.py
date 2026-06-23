"""
gui.py

Main Tkinter GUI for DumperYTPGen.

Implements:
- Menu bar
- Notebook tabs: Sources, Auto Clips, Effects, Audio, Overlays, Export
- Status bar and progress
- Threaded operations
- Preview, import, remove, save/load project
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import threading
import os
import traceback
import time
from pathlib import Path
import json
import random
import shutil

from config import ConfigManager
from library_manager import LibraryManager
from ffmpeg_utils import FFmpegUtils, FFmpegNotFoundError
from clip_generator import ClipGenerator
from effect_engine import EffectEngine
from project_manager import ProjectManager
from export_manager import ExportManager


class DumperYTPApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DumperYTPGen")
        self.geometry("1100x700")
        self.minsize(900, 600)

        # Core managers
        self.config_manager = ConfigManager()
        try:
            self.ffutils = FFmpegUtils(self.config_manager)
        except FFmpegNotFoundError as e:
            # Allow app to open but show warning
            self.ffutils = None
            messagebox.showwarning("FFmpeg not found", str(e))

        self.library = LibraryManager(self.config_manager)
        self.clipgen = ClipGenerator(self.config_manager, self.ffutils) if self.ffutils else None
        self.effect_engine = EffectEngine(self.ffutils) if self.ffutils else None
        self.project_manager = ProjectManager(self.config_manager)
        self.export_manager = ExportManager(self.config_manager, self.ffutils) if self.ffutils else None

        self._build_menu()
        self._build_ui()
        self._build_statusbar()

        # State
        self.generated_clips = []  # list of clip descriptors
        self.current_project = {}
        self.cancel_event = threading.Event()

        # seed
        seed = self.config_manager.get("random_seed")
        if seed:
            random.seed(seed)

    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="New Project", command=self.new_project)
        filemenu.add_command(label="Open Project...", command=self.open_project)
        filemenu.add_command(label="Save Project...", command=self.save_project)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=filemenu)

        configmenu = tk.Menu(menubar, tearoff=0)
        configmenu.add_command(label="Set FFmpeg Path...", command=self.set_ffmpeg_path)
        configmenu.add_command(label="Set FFprobe Path...", command=self.set_ffprobe_path)
        menubar.add_cascade(label="Config", menu=configmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=lambda: messagebox.showinfo("About", "DumperYTPGen\nA YTP auto-generator"))
        menubar.add_cascade(label="Help", menu=helpmenu)

        # Use Tk.config to set the menu (no name conflict now)
        self.config(menu=menubar)

    def _build_ui(self):
        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_sources_tab()
        self._build_auto_clips_tab()
        self._build_effects_tab()
        self._build_audio_tab()
        self._build_overlays_tab()
        self._build_export_tab()

    def _build_statusbar(self):
        frame = ttk.Frame(self)
        frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0.0)
        lbl = ttk.Label(frame, textvariable=self.status_var)
        lbl.pack(side=tk.LEFT, padx=5)
        self.progress = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5, pady=4)

    # ---------- Sources Tab ----------
    def _build_sources_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Sources")

        left = ttk.Frame(tab)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        right = ttk.Frame(tab)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)

        # library listboxes
        self.lib_tabs = ttk.Notebook(left)
        self.lib_tabs.pack(fill=tk.BOTH, expand=True)

        self.lib_listboxes = {}
        for lib in ["videos", "audios", "images", "sfx", "dance", "transitions"]:
            frame = ttk.Frame(self.lib_tabs)
            self.lib_tabs.add(frame, text=lib.capitalize())
            lb = tk.Listbox(frame, selectmode=tk.SINGLE)
            lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar = ttk.Scrollbar(frame, command=lb.yview)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            lb.config(yscrollcommand=scrollbar.set)
            self.lib_listboxes[lib] = lb

            # populate
            for p in self.config_manager.get_library(lib):
                lb.insert(tk.END, p)

        # Controls
        ttk.Button(right, text="Import...", command=self.import_files_dialog).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="Remove Selected", command=self.remove_selected_library_item).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="Preview Selected", command=self.preview_selected).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="Clear Temp Files", command=self.clear_temp_files).pack(fill=tk.X, pady=2)
        ttk.Button(right, text="Recent Projects", command=self.show_recent_projects).pack(fill=tk.X, pady=2)

    def import_files_dialog(self):
        current_tab = self.lib_tabs.tab(self.lib_tabs.select(), "text").lower()
        filetypes = [("All files", "*.*")]
        if current_tab in ("videos",):
            filetypes = [("Video files", "*.mp4;*.mov;*.avi;*.mkv"), ("All files", "*.*")]
        elif current_tab in ("audios", "sfx", "dance"):
            filetypes = [("Audio files", "*.mp3;*.wav;*.aac;*.m4a"), ("All files", "*.*")]
        elif current_tab in ("images",):
            filetypes = [("Image files", "*.png;*.jpg;*.jpeg;*.bmp;*.gif")]
        paths = filedialog.askopenfilenames(title="Import files", filetypes=filetypes)
        for p in paths:
            try:
                self.library.add(current_tab, p)
                self.lib_listboxes[current_tab].insert(tk.END, p)
            except Exception as e:
                messagebox.showerror("Import failed", str(e))

    def remove_selected_library_item(self):
        current_tab = self.lib_tabs.tab(self.lib_tabs.select(), "text").lower()
        lb = self.lib_listboxes[current_tab]
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        path = lb.get(idx)
        self.library.remove(current_tab, path)
        lb.delete(idx)

    def preview_selected(self):
        current_tab = self.lib_tabs.tab(self.lib_tabs.select(), "text").lower()
        lb = self.lib_listboxes[current_tab]
        sel = lb.curselection()
        if not sel:
            return
        path = lb.get(sel[0])
        try:
            self.library.preview(path)
        except Exception as e:
            messagebox.showerror("Preview failed", str(e))

    def clear_temp_files(self):
        if not self.clipgen:
            return
        self.clipgen.clear_temp()
        messagebox.showinfo("Temp Cleared", "Temporary generated files have been removed.")

    def show_recent_projects(self):
        rp = self.project_manager.list_recent()
        if not rp:
            messagebox.showinfo("Recent Projects", "No recent projects.")
            return
        # show a simple selection dialog
        top = tk.Toplevel(self)
        top.title("Recent Projects")
        lb = tk.Listbox(top)
        lb.pack(fill=tk.BOTH, expand=True)
        for p in rp:
            lb.insert(tk.END, p)

    # ---------- Auto Clips Tab ----------
    def _build_auto_clips_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Auto Clips")

        frm = ttk.Frame(tab)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Min clip length (s):").grid(row=0, column=0, sticky=tk.W)
        self.min_len_var = tk.DoubleVar(value=0.5)
        ttk.Entry(frm, textvariable=self.min_len_var, width=10).grid(row=0, column=1, sticky=tk.W)

        ttk.Label(frm, text="Max clip length (s):").grid(row=1, column=0, sticky=tk.W)
        self.max_len_var = tk.DoubleVar(value=3.0)
        ttk.Entry(frm, textvariable=self.max_len_var, width=10).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(frm, text="Number of clips:").grid(row=2, column=0, sticky=tk.W)
        self.count_var = tk.IntVar(value=20)
        ttk.Entry(frm, textvariable=self.count_var, width=10).grid(row=2, column=1, sticky=tk.W)

        ttk.Button(frm, text="Generate Clips", command=self.generate_clips).grid(row=3, column=0, columnspan=2, pady=5)

        self.clips_listbox = tk.Listbox(frm)
        self.clips_listbox.grid(row=0, column=2, rowspan=6, sticky=tk.NSEW, padx=10)
        frm.grid_columnconfigure(2, weight=1)
        frm.grid_rowconfigure(5, weight=1)

        ttk.Button(frm, text="Remove Selected Clip", command=self.remove_selected_generated_clip).grid(row=6, column=2, sticky=tk.EW, pady=5)
        ttk.Button(frm, text="Preview Clip", command=self.preview_generated_clip).grid(row=7, column=2, sticky=tk.EW, pady=5)

    def generate_clips(self):
        if not self.clipgen:
            messagebox.showerror("FFmpeg Missing", "FFmpeg utilities unavailable. Configure paths first.")
            return
        videos = self.config_manager.get_library("videos")
        if not videos:
            messagebox.showerror("No sources", "No video sources in library.")
            return
        min_len = float(self.min_len_var.get())
        max_len = float(self.max_len_var.get())
        count = int(self.count_var.get())
        self.status_var.set("Generating clips...")
        self.progress_var.set(0.0)
        self.clips_listbox.delete(0, tk.END)
        self.generated_clips = []
        self.cancel_event.clear()

        def on_progress(idx, total, path):
            # Some ClipGenerator callbacks use (idx, total, path)
            try:
                self.progress_var.set(100.0 * idx / total)
                self.status_var.set(f"Generated {idx}/{total}")
                self.clips_listbox.insert(tk.END, path)
                self.generated_clips.append(path)
            except Exception:
                pass

        def worker():
            try:
                clips = self.clipgen.generate_random_clips(videos, count, min_len, max_len, progress_callback=on_progress, cancel_event=self.cancel_event)
                # update GUI state on main thread
                self.status_var.set(f"Generated {len(clips)} clips")
            except Exception as e:
                self.status_var.set("Error generating clips")
                messagebox.showerror("Error", str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def remove_selected_generated_clip(self):
        sel = self.clips_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        path = self.clips_listbox.get(idx)
        try:
            os.remove(path)
        except Exception:
            pass
        self.clips_listbox.delete(idx)
        if path in self.generated_clips:
            self.generated_clips.remove(path)

    def preview_generated_clip(self):
        sel = self.clips_listbox.curselection()
        if not sel:
            return
        path = self.clips_listbox.get(sel[0])
        try:
            self.library.preview(path)
        except Exception as e:
            messagebox.showerror("Preview failed", str(e))

    # ---------- Effects Tab ----------
    def _build_effects_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Effects")

        frm = ttk.Frame(tab)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Effects list
        self.effects_list = tk.Listbox(frm, selectmode=tk.MULTIPLE)
        effects = [
            "speed_change",
            "reverse",
            "freeze_frame",
            "ear_rape",
            "repeat_word",
            "zoom_spin_mirror_rgb",
            "subtitle_random",
        ]
        for e in effects:
            self.effects_list.insert(tk.END, e)
        self.effects_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ctrls = ttk.Frame(frm)
        ctrls.pack(side=tk.RIGHT, fill=tk.Y, padx=5)

        ttk.Button(ctrls, text="Apply to Selected Clip", command=self.apply_effects_to_clip).pack(fill=tk.X, pady=2)
        ttk.Button(ctrls, text="Apply Random Effects to All Clips", command=self.apply_random_effects_to_all).pack(fill=tk.X, pady=2)
        ttk.Button(ctrls, text="Shuffle Clips", command=self.shuffle_generated_clips).pack(fill=tk.X, pady=2)

    def apply_effects_to_clip(self):
        sel = self.clips_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select Clip", "Select a generated clip first.")
            return
        idx = sel[0]
        path = self.clips_listbox.get(idx)
        chosen = [self.effects_list.get(i) for i in self.effects_list.curselection()]
        if not chosen:
            messagebox.showinfo("Select Effect", "Select one or more effects.")
            return

        def worker():
            try:
                basedst = str(Path(path).with_suffix(f".effected.mp4"))
                tmpsrc = path
                for e in chosen:
                    dst = str(Path(basedst).with_suffix(f".{e}.mp4"))
                    if e == "speed_change":
                        spd = random.choice([0.5, 0.75, 1.25, 1.5, 2.0])
                        self.effect_engine.apply_speed_change(tmpsrc, dst, speed=spd)
                    elif e == "reverse":
                        self.effect_engine.apply_reverse(tmpsrc, dst)
                    elif e == "freeze_frame":
                        self.effect_engine.apply_freeze_frame(tmpsrc, dst, freeze_time=0.6)
                    elif e == "ear_rape":
                        self.effect_engine.apply_ear_rape(tmpsrc, dst, db_boost=18.0)
                    elif e == "repeat_word":
                        self.effect_engine.apply_repeat_word_effect(tmpsrc, dst, repeat_count=3)
                    elif e == "zoom_spin_mirror_rgb":
                        self.effect_engine.apply_zoom_spin_mirror_rgb(tmpsrc, dst, zoom=1.2, spin=20, mirror=random.choice([True, False]), rgb_split=random.choice([True, False]))
                    elif e == "subtitle_random":
                        text = random.choice(["WOW", "WHAT THE", "NOPE", "YTP LOL", "MEME"])
                        pos = random.choice(["top-left", "top-right", "center", "bottom-left", "bottom-right"])
                        self.effect_engine.add_subtitle_text(tmpsrc, dst, text, position=pos)
                    else:
                        shutil.copy(tmpsrc, dst)
                    tmpsrc = dst
                # finally, replace item in list with final dst
                self.clips_listbox.delete(idx)
                self.clips_listbox.insert(idx, tmpsrc)
            except Exception as e:
                messagebox.showerror("Error", str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def apply_random_effects_to_all(self):
        all_items = list(self.clips_listbox.get(0, tk.END))
        if not all_items:
            messagebox.showinfo("No Clips", "No generated clips to affect.")
            return

        def worker():
            try:
                for i, path in enumerate(all_items):
                    chosen = random.sample(["speed_change", "reverse", "freeze_frame", "ear_rape", "subtitle_random"], k=random.randint(1, 2))
                    tmpsrc = path
                    for e in chosen:
                        dst = str(Path(tmpsrc).with_suffix(f".{e}.mp4"))
                        if e == "speed_change":
                            spd = random.choice([0.5, 0.75, 1.25, 1.5, 2.0])
                            self.effect_engine.apply_speed_change(tmpsrc, dst, speed=spd)
                        elif e == "reverse":
                            self.effect_engine.apply_reverse(tmpsrc, dst)
                        elif e == "freeze_frame":
                            self.effect_engine.apply_freeze_frame(tmpsrc, dst, freeze_time=0.6)
                        elif e == "ear_rape":
                            self.effect_engine.apply_ear_rape(tmpsrc, dst, db_boost=18.0)
                        elif e == "subtitle_random":
                            text = random.choice(["WOW", "WHAT THE", "NOPE", "YTP LOL", "MEME"])
                            pos = random.choice(["top-left", "top-right", "center", "bottom-left", "bottom-right"])
                            self.effect_engine.add_subtitle_text(tmpsrc, dst, text, position=pos)
                        tmpsrc = dst
                    # update listbox
                    self.clips_listbox.delete(i)
                    self.clips_listbox.insert(i, tmpsrc)
            except Exception as e:
                messagebox.showerror("Error", str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def shuffle_generated_clips(self):
        items = list(self.clips_listbox.get(0, tk.END))
        random.shuffle(items)
        self.clips_listbox.delete(0, tk.END)
        for it in items:
            self.clips_listbox.insert(tk.END, it)

    # ---------- Audio Tab ----------
    def _build_audio_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Audio")

        frm = ttk.Frame(tab)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # dance music mode
        ttk.Label(frm, text="Dance Music Mode: Select music track").grid(row=0, column=0, sticky=tk.W)
        self.dance_listbox = tk.Listbox(frm)
        for p in self.config_manager.get_library("dance"):
            self.dance_listbox.insert(tk.END, p)
        self.dance_listbox.grid(row=1, column=0, sticky=tk.NSEW)
        frm.grid_rowconfigure(1, weight=1)
        frm.grid_columnconfigure(0, weight=1)

        ttk.Label(frm, text="Music volume:").grid(row=0, column=1, sticky=tk.W)
        self.music_volume = tk.DoubleVar(value=0.8)
        ttk.Scale(frm, variable=self.music_volume, from_=0.0, to=2.0, orient=tk.HORIZONTAL).grid(row=1, column=1, sticky=tk.EW)

        ttk.Button(frm, text="Auto-cut to beat (fast montage)", command=self.dance_mode_generate).grid(row=2, column=0, columnspan=2, pady=5)

    def dance_mode_generate(self):
        sel = self.dance_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select Music", "Select a music track for dance mode.")
            return
        music = self.dance_listbox.get(sel[0])
        # naive beat cut: take generated clips and cut each to 1 beat length (e.g., 0.5s) and sync
        beat = 0.5
        items = list(self.clips_listbox.get(0, tk.END))
        if not items:
            messagebox.showinfo("No Clips", "No generated clips to montage.")
            return

        def worker():
            try:
                self.status_var.set("Generating dance montage...")
                out_clips = []
                for i, c in enumerate(items):
                    dst = str(Path(c).with_suffix(f".beat{i}.mp4"))
                    # trim to beat
                    self.ffutils.build_trim_clip(c, 0, beat, dst)
                    out_clips.append(dst)
                # concat
                out_final = str(Path(self.config_manager.get("temp_dir")) / f"dance_{int(time.time())}.mp4")
                self.ffutils.concat_clips(out_clips, out_final)
                messagebox.showinfo("Dance Montage Ready", f"Dance montage created: {out_final}\nYou can preview or export it.")
                self.status_var.set("Dance montage ready")
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self.status_var.set("Error in dance mode")

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    # ---------- Overlays Tab ----------
    def _build_overlays_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Overlays")

        frm = ttk.Frame(tab)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Overlay images/videos (from Images/Transitions libraries)").pack()
        self.overlays_listbox = tk.Listbox(frm)
        for p in self.config_manager.get_library("images") + self.config_manager.get_library("transitions"):
            self.overlays_listbox.insert(tk.END, p)
        self.overlays_listbox.pack(fill=tk.BOTH, expand=True)
        ttk.Button(frm, text="Add Random Overlay to Project", command=self.add_random_overlay).pack(pady=2)

        # overlay actions
        ttk.Label(frm, text="Overlay duration (s):").pack()
        self.overlay_duration = tk.DoubleVar(value=1.0)
        ttk.Entry(frm, textvariable=self.overlay_duration, width=10).pack()

        ttk.Label(frm, text="Overlay position:").pack()
        self.overlay_pos = tk.StringVar(value="center")
        ttk.Combobox(frm, textvariable=self.overlay_pos, values=["top-left", "top-right", "center", "bottom-left", "bottom-right"]).pack()

        self.project_overlays = []

    def add_random_overlay(self):
        sel = self.overlays_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select Overlay", "Select an overlay from the list.")
            return
        path = self.overlays_listbox.get(sel[0])
        entry = {"path": path, "duration": float(self.overlay_duration.get()), "position": self.overlay_pos.get()}
        self.project_overlays.append(entry)
        messagebox.showinfo("Overlay Added", f"Overlay added: {path}")

    # ---------- Export Tab ----------
    def _build_export_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Export")

        frm = ttk.Frame(tab)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Output file:").grid(row=0, column=0, sticky=tk.W)
        self.output_path_var = tk.StringVar(value=str(Path(self.config_manager.get("last_project_dir")) / "output.mp4"))
        ttk.Entry(frm, textvariable=self.output_path_var, width=60).grid(row=0, column=1, sticky=tk.EW)
        ttk.Button(frm, text="Browse...", command=self.browse_output).grid(row=0, column=2, sticky=tk.E)

        ttk.Label(frm, text="Bitrate (e.g., 2000k):").grid(row=1, column=0, sticky=tk.W)
        self.bitrate_var = tk.StringVar(value="2000k")
        ttk.Entry(frm, textvariable=self.bitrate_var, width=10).grid(row=1, column=1, sticky=tk.W)

        ttk.Button(frm, text="Export YTP", command=self.export_ytp).grid(row=2, column=0, pady=5)
        ttk.Button(frm, text="Cancel Export", command=self.cancel_export).grid(row=2, column=1, pady=5)

        self.export_log = tk.Text(frm, height=10)
        self.export_log.grid(row=3, column=0, columnspan=3, sticky=tk.NSEW)
        frm.grid_rowconfigure(3, weight=1)
        frm.grid_columnconfigure(1, weight=1)

    def browse_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")])
        if path:
            self.output_path_var.set(path)
            self.config_manager.set("last_project_dir", str(Path(path).parent))

    def export_ytp(self):
        if not self.export_manager:
            messagebox.showerror("FFmpeg Missing", "FFmpeg utilities unavailable. Configure paths first.")
            return
        clips = list(self.clips_listbox.get(0, tk.END))
        if not clips:
            messagebox.showinfo("No clips", "No clips to export.")
            return
        output = self.output_path_var.get()
        bitrate = self.bitrate_var.get()
        overlays = self.project_overlays
        sfx_entries = []  # for now, none
        music = None  # optional

        self.export_log.delete("1.0", tk.END)
        self.status_var.set("Starting export...")
        self.progress_var.set(0.0)
        if self.export_manager:
            self.export_manager._cancel_event.clear()

        def on_progress(seconds):
            # naive percent mapping: since we don't know total, just show animated progress
            self.progress_var.set((self.progress_var.get() + 1) % 100)

        def on_log(line):
            self.export_log.insert(tk.END, line + "\n")
            self.export_log.see(tk.END)

        if self.export_manager:
            self.export_manager.export_project(clips, overlays, sfx_entries, music, output, bitrate=bitrate, on_progress=on_progress, on_log=on_log)
            self.status_var.set("Export running in background")
        else:
            messagebox.showerror("Export unavailable", "Export manager not initialized (FFmpeg missing).")

    def cancel_export(self):
        if self.export_manager:
            self.export_manager.cancel()
            self.status_var.set("Cancel requested")

    # ---------- Project operations ----------
    def new_project(self):
        self.current_project = {}
        self.clips_listbox.delete(0, tk.END)
        self.project_overlays = []
        self.status_var.set("New project")

    def open_project(self):
        path = filedialog.askopenfilename(title="Open Project", filetypes=[("DumperYTPGen Project", "*.json")])
        if not path:
            return
        try:
            data = self.project_manager.load_project(path)
            # basic restore: clips and overlays
            self.clips_listbox.delete(0, tk.END)
            for c in data.get("clips", []):
                self.clips_listbox.insert(tk.END, c)
            self.project_overlays = data.get("overlays", [])
            self.status_var.set(f"Project loaded: {path}")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def save_project(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("DumperYTPGen Project", "*.json")])
        if not path:
            return
        data = {
            "clips": list(self.clips_listbox.get(0, tk.END)),
            "overlays": self.project_overlays,
            "settings": {"bitrate": self.bitrate_var.get()},
        }
        try:
            self.project_manager.save_project(path, data)
            self.status_var.set(f"Project saved: {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # ---------- Config dialogs ----------
    def set_ffmpeg_path(self):
        path = filedialog.askopenfilename(title="Select ffmpeg executable")
        if path:
            self.config_manager.set("ffmpeg_path", path)
            messagebox.showinfo("Saved", "FFmpeg path saved; restart app to re-detect.")

    def set_ffprobe_path(self):
        path = filedialog.askopenfilename(title="Select ffprobe executable")
        if path:
            self.config_manager.set("ffprobe_path", path)
            messagebox.showinfo("Saved", "FFprobe path saved; restart app to re-detect.")


def main():
    app = DumperYTPApp()
    app.mainloop()


if __name__ == "__main__":
    main()