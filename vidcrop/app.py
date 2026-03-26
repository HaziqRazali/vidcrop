#!/usr/bin/env python3
"""
vidcrop – Video Trim & Crop GUI
Load a video, set start/end points with the slider, draw a bounding box, then trim & crop.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import cv2
from PIL import Image, ImageTk
import os
import threading


CANVAS_MAX_W = 960
CANVAS_MAX_H = 540


class VideoTrimCropApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("vidcrop – Video Trim & Crop")
        self.root.resizable(True, True)

        # Video state
        self.cap: cv2.VideoCapture | None = None
        self.video_path: str = ""
        self.total_frames: int = 0
        self.fps: float = 30.0
        self.vid_w: int = 0
        self.vid_h: int = 0
        self.canvas_w: int = CANVAS_MAX_W
        self.canvas_h: int = CANVAS_MAX_H
        self.scale_x: float = 1.0  # canvas -> video
        self.scale_y: float = 1.0

        self.current_frame_idx: int = 0
        self.start_frame: int = 0
        self.end_frame: int = 0

        # Bounding box (in canvas coords)
        self.bbox_canvas: tuple[int, int, int, int] | None = None
        self.drag_origin: tuple[int, int] | None = None
        self.is_dragging: bool = False

        # Zoom / pan state (all in normalised 0-1 video space)
        self._zoom: float = 1.0          # 1.0 = full frame, >1 = zoomed in
        self._pan_x: float = 0.5         # centre of view, normalised
        self._pan_y: float = 0.5
        self._pan_drag_origin: tuple[int, int] | None = None  # canvas px
        self._pan_drag_start: tuple[float, float] = (0.5, 0.5)  # pan at drag start

        # Current displayed photo image (keep reference to prevent GC)
        self._photo: ImageTk.PhotoImage | None = None
        # Raw full-resolution frame stored as numpy (BGR)
        self._current_frame_bgr: object = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────
        top = tk.Frame(self.root, pady=4)
        top.pack(side=tk.TOP, fill=tk.X, padx=8)

        tk.Button(top, text="Open Video", command=self._open_video,
                  width=12).pack(side=tk.LEFT, padx=4)
        self.lbl_file = tk.Label(top, text="No file loaded", anchor="w",
                                 fg="gray", width=60)
        self.lbl_file.pack(side=tk.LEFT, padx=4)

        # ── Canvas ───────────────────────────────────────────────────
        canvas_frame = tk.Frame(self.root, bg="black")
        canvas_frame.pack(side=tk.TOP, padx=8, pady=4)

        self.canvas = tk.Canvas(canvas_frame, width=CANVAS_MAX_W,
                                height=CANVAS_MAX_H, bg="black",
                                cursor="crosshair")
        self.canvas.pack()

        self.canvas.bind("<ButtonPress-1>",   self._on_mouse_press)
        self.canvas.bind("<B1-Motion>",          self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>",    self._on_mouse_release)
        # Zoom: scroll wheel (Linux Button-4/5, Windows/Mac MouseWheel)
        self.canvas.bind("<Button-4>",           self._on_scroll_up)
        self.canvas.bind("<Button-5>",           self._on_scroll_down)
        self.canvas.bind("<MouseWheel>",         self._on_mousewheel)
        # Pan: middle mouse button drag
        self.canvas.bind("<ButtonPress-2>",      self._on_pan_press)
        self.canvas.bind("<B2-Motion>",          self._on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>",    self._on_pan_release)

        # Placeholder text
        self.canvas.create_text(CANVAS_MAX_W // 2, CANVAS_MAX_H // 2,
                                text="Open a video to begin",
                                fill="gray", font=("Helvetica", 16),
                                tags="placeholder")

        # ── Slider + time labels ──────────────────────────────────────
        slider_frame = tk.Frame(self.root)
        slider_frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=2)

        self.lbl_current = tk.Label(slider_frame, text="00:00.000", width=9)
        self.lbl_current.pack(side=tk.LEFT)

        self.slider = ttk.Scale(slider_frame, from_=0, to=1,
                                orient=tk.HORIZONTAL,
                                command=self._on_slider_move)
        self.slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.slider.state(["disabled"])

        self.lbl_total = tk.Label(slider_frame, text="00:00.000", width=9)
        self.lbl_total.pack(side=tk.LEFT)

        # ── Start / End controls ─────────────────────────────────────
        ctrl_frame = tk.Frame(self.root)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=4)

        tk.Button(ctrl_frame, text="Set Start Here", bg="#2a7a2a", fg="white",
                  width=14, command=self._set_start).pack(side=tk.LEFT, padx=4)
        self.lbl_start = tk.Label(ctrl_frame, text="Start: --", width=16,
                                  anchor="w", fg="#2a7a2a")
        self.lbl_start.pack(side=tk.LEFT)

        tk.Button(ctrl_frame, text="Set End Here", bg="#7a2a2a", fg="white",
                  width=14, command=self._set_end).pack(side=tk.LEFT, padx=8)
        self.lbl_end = tk.Label(ctrl_frame, text="End: --", width=16,
                                anchor="w", fg="#7a2a2a")
        self.lbl_end.pack(side=tk.LEFT)

        tk.Button(ctrl_frame, text="Clear BBox", width=10,
                  command=self._clear_bbox).pack(side=tk.LEFT, padx=8)
        self.lbl_bbox = tk.Label(ctrl_frame, text="BBox: none", width=24,
                                 anchor="w", fg="gray")
        self.lbl_bbox.pack(side=tk.LEFT)

        tk.Button(ctrl_frame, text="Reset Zoom", width=10,
                  command=self._reset_zoom).pack(side=tk.LEFT, padx=8)
        self.lbl_zoom = tk.Label(ctrl_frame, text="Zoom: 1.0x", width=12,
                                 anchor="w", fg="gray")
        self.lbl_zoom.pack(side=tk.LEFT)

        # ── Action row ───────────────────────────────────────────────
        action_frame = tk.Frame(self.root)
        action_frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        self.btn_trim = tk.Button(action_frame, text="Trim & Crop",
                                  bg="#1a5fa8", fg="white",
                                  font=("Helvetica", 11, "bold"),
                                  width=16, command=self._trim_and_crop,
                                  state=tk.DISABLED)
        self.btn_trim.pack(side=tk.LEFT, padx=4)

        self.lbl_status = tk.Label(action_frame, text="", fg="gray",
                                   font=("Helvetica", 10))
        self.lbl_status.pack(side=tk.LEFT, padx=8)

        self.progress = ttk.Progressbar(action_frame, length=200,
                                        mode="determinate")
        self.progress.pack(side=tk.LEFT, padx=4)

    # ------------------------------------------------------------------
    # File Loading
    # ------------------------------------------------------------------
    def _open_video(self):
        path = filedialog.askopenfilename(
            title="Select video",
            filetypes=[("Video files",
                        "*.mp4 *.avi *.mov *.mkv *.webm *.flv *.ts *.mxf"),
                       ("All files", "*.*")]
        )
        if not path:
            return

        if self.cap:
            self.cap.release()

        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            messagebox.showerror("Error", f"Cannot open video:\n{path}")
            return

        self.video_path = path
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.vid_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.vid_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Compute canvas size keeping aspect ratio
        aspect = self.vid_w / max(self.vid_h, 1)
        if self.vid_w > CANVAS_MAX_W or self.vid_h > CANVAS_MAX_H:
            if aspect > CANVAS_MAX_W / CANVAS_MAX_H:
                self.canvas_w = CANVAS_MAX_W
                self.canvas_h = int(CANVAS_MAX_W / aspect)
            else:
                self.canvas_h = CANVAS_MAX_H
                self.canvas_w = int(CANVAS_MAX_H * aspect)
        else:
            self.canvas_w = self.vid_w
            self.canvas_h = self.vid_h

        self.scale_x = self.vid_w / self.canvas_w
        self.scale_y = self.vid_h / self.canvas_h

        self.canvas.config(width=self.canvas_w, height=self.canvas_h)

        # Reset state
        self.start_frame = 0
        self.end_frame = self.total_frames - 1
        self.bbox_canvas = None
        self.current_frame_idx = 0
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5

        self.slider.config(to=self.total_frames - 1)
        self.slider.set(0)
        self.slider.state(["!disabled"])

        dur = self.total_frames / self.fps
        self.lbl_total.config(text=self._fmt_time(dur))
        self.lbl_file.config(text=os.path.basename(path), fg="black")
        self.lbl_start.config(text=f"Start: {self._fmt_time(0)}")
        self.lbl_end.config(
            text=f"End: {self._fmt_time(self.end_frame / self.fps)}")
        self.lbl_bbox.config(text="BBox: none", fg="gray")
        self.btn_trim.config(state=tk.NORMAL)

        self.canvas.delete("placeholder")
        self._show_frame(0)

    # ------------------------------------------------------------------
    # Frame Display
    # ------------------------------------------------------------------
    def _show_frame(self, idx: int):
        if self.cap is None:
            return
        idx = max(0, min(idx, self.total_frames - 1))
        self.current_frame_idx = idx
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = self.cap.read()
        if not ret:
            return

        self._current_frame_bgr = frame  # store full-res BGR
        self._render_canvas()

        self.lbl_current.config(text=self._fmt_time(idx / self.fps))
        # Update slider without triggering callback
        self.slider.set(idx)

    # ------------------------------------------------------------------
    # Zoom / Pan
    # ------------------------------------------------------------------
    def _view_rect(self) -> tuple[int, int, int, int]:
        """Return (x1,y1,x2,y2) in video pixels for the current zoom/pan."""
        view_w = self.vid_w / self._zoom
        view_h = self.vid_h / self._zoom
        cx = self._pan_x * self.vid_w
        cy = self._pan_y * self.vid_h
        x1 = cx - view_w / 2
        y1 = cy - view_h / 2
        # clamp so we never go out of bounds
        x1 = max(0.0, min(x1, self.vid_w - view_w))
        y1 = max(0.0, min(y1, self.vid_h - view_h))
        x2 = x1 + view_w
        y2 = y1 + view_h
        return int(x1), int(y1), int(x2), int(y2)

    def _on_scroll_up(self, event):
        self._apply_zoom(1.15, event.x, event.y)

    def _on_scroll_down(self, event):
        self._apply_zoom(1 / 1.15, event.x, event.y)

    def _on_mousewheel(self, event):
        """Windows/macOS scroll wheel."""
        if event.delta > 0:
            self._apply_zoom(1.15, event.x, event.y)
        else:
            self._apply_zoom(1 / 1.15, event.x, event.y)

    def _apply_zoom(self, factor: float, cx: int, cy: int):
        """Zoom by `factor` keeping canvas point (cx, cy) fixed."""
        if self._current_frame_bgr is None:
            return
        # Convert canvas cursor to normalised video space before zoom
        vr = self._view_rect()
        vw = vr[2] - vr[0]
        vh = vr[3] - vr[1]
        norm_x = vr[0] / self.vid_w + (cx / self.canvas_w) * (vw / self.vid_w)
        norm_y = vr[1] / self.vid_h + (cy / self.canvas_h) * (vh / self.vid_h)

        new_zoom = max(1.0, min(self._zoom * factor, 20.0))
        self._zoom = new_zoom

        # Shift pan so the point under the cursor stays fixed
        new_vw = self.vid_w / self._zoom
        new_vh = self.vid_h / self._zoom
        self._pan_x = norm_x + (0.5 - cx / self.canvas_w) * (new_vw / self.vid_w)
        self._pan_y = norm_y + (0.5 - cy / self.canvas_h) * (new_vh / self.vid_h)
        # Clamp pan so view stays inside the frame
        half_w = (self.vid_w / self._zoom) / 2 / self.vid_w
        half_h = (self.vid_h / self._zoom) / 2 / self.vid_h
        self._pan_x = max(half_w, min(self._pan_x, 1.0 - half_w))
        self._pan_y = max(half_h, min(self._pan_y, 1.0 - half_h))

        self.lbl_zoom.config(text=f"Zoom: {self._zoom:.1f}x")
        self._render_canvas()

    def _on_pan_press(self, event):
        self._pan_drag_origin = (event.x, event.y)
        self._pan_drag_start = (self._pan_x, self._pan_y)

    def _on_pan_drag(self, event):
        if self._pan_drag_origin is None or self._current_frame_bgr is None:
            return
        dx = event.x - self._pan_drag_origin[0]
        dy = event.y - self._pan_drag_origin[1]
        # Convert canvas delta to normalised video delta
        vr = self._view_rect()
        vw = vr[2] - vr[0]
        vh = vr[3] - vr[1]
        self._pan_x = self._pan_drag_start[0] - dx * (vw / self.vid_w) / self.canvas_w
        self._pan_y = self._pan_drag_start[1] - dy * (vh / self.vid_h) / self.canvas_h
        # Clamp
        half_w = (self.vid_w / self._zoom) / 2 / self.vid_w
        half_h = (self.vid_h / self._zoom) / 2 / self.vid_h
        self._pan_x = max(half_w, min(self._pan_x, 1.0 - half_w))
        self._pan_y = max(half_h, min(self._pan_y, 1.0 - half_h))
        self._render_canvas()

    def _on_pan_release(self, event):
        self._pan_drag_origin = None

    def _reset_zoom(self):
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self.lbl_zoom.config(text="Zoom: 1.0x")
        self._render_canvas()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_canvas(self):
        """Re-draw the canvas from the stored raw frame + zoom/pan + bbox overlay."""
        if self._current_frame_bgr is None:
            return
        # Crop to current view rect and resize to canvas
        x1, y1, x2, y2 = self._view_rect()
        cropped = self._current_frame_bgr[y1:y2, x1:x2]
        frame_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb).resize(
            (self.canvas_w, self.canvas_h), Image.LANCZOS)

        if self.bbox_canvas is not None:
            x1, y1, x2, y2 = self._normalise_bbox(self.bbox_canvas)
            # Draw semi-transparent fill
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(overlay)
            draw.rectangle([x1, y1, x2, y2], outline=(0, 220, 255),
                           width=2, fill=(0, 220, 255, 40))
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, overlay).convert("RGB")

            # Corner markers
            draw2 = ImageDraw.ImageDraw(img)
            corner_len = 10
            c = (0, 220, 255)
            for cx, cy, dx, dy in [
                (x1, y1, 1, 1), (x2, y1, -1, 1),
                (x1, y2, 1, -1), (x2, y2, -1, -1)
            ]:
                draw2.line([(cx, cy), (cx + dx * corner_len, cy)],
                           fill=c, width=3)
                draw2.line([(cx, cy), (cx, cy + dy * corner_len)],
                           fill=c, width=3)

        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, anchor=tk.NW,
                                 image=self._photo, tags="frame")
        self.canvas.tag_lower("frame")  # keep bbox rect on top if drawn

    # ------------------------------------------------------------------
    # Slider
    # ------------------------------------------------------------------
    def _on_slider_move(self, val):
        idx = int(float(val))
        if idx != self.current_frame_idx:
            self._show_frame(idx)

    # ------------------------------------------------------------------
    # Start / End
    # ------------------------------------------------------------------
    def _set_start(self):
        self.start_frame = self.current_frame_idx
        t = self._fmt_time(self.start_frame / self.fps)
        self.lbl_start.config(text=f"Start: {t}")
        self._set_status(f"Start set: {t}")

    def _set_end(self):
        self.end_frame = self.current_frame_idx
        t = self._fmt_time(self.end_frame / self.fps)
        self.lbl_end.config(text=f"End: {t}")
        self._set_status(f"End set: {t}")

    # ------------------------------------------------------------------
    # Bounding Box (mouse events on canvas)
    # ------------------------------------------------------------------
    def _on_mouse_press(self, event):
        self.is_dragging = True
        self.drag_origin = (event.x, event.y)
        self.bbox_canvas = None

    def _on_mouse_drag(self, event):
        if not self.is_dragging or self.drag_origin is None:
            return
        x0, y0 = self.drag_origin
        self.bbox_canvas = (x0, y0, event.x, event.y)
        self._render_canvas()

    def _on_mouse_release(self, event):
        if not self.is_dragging or self.drag_origin is None:
            return
        self.is_dragging = False
        x0, y0 = self.drag_origin
        self.bbox_canvas = (x0, y0, event.x, event.y)
        self._render_canvas()

        # Map to video coords
        bv = self._bbox_to_video(self.bbox_canvas)
        self.lbl_bbox.config(
            text=f"BBox: ({bv[0]},{bv[1]}) → ({bv[2]},{bv[3]})", fg="#1a5fa8")
        self._set_status(
            f"BBox set: x={bv[0]}-{bv[2]}, y={bv[1]}-{bv[3]}")

    def _clear_bbox(self):
        self.bbox_canvas = None
        self._render_canvas()
        self.lbl_bbox.config(text="BBox: none (full frame)", fg="gray")

    # ------------------------------------------------------------------
    # Trim & Crop
    # ------------------------------------------------------------------
    def _trim_and_crop(self):
        if self.cap is None:
            return
        if self.start_frame >= self.end_frame:
            messagebox.showwarning(
                "Invalid range",
                "Start frame must be before end frame.\nPlease adjust the start/end points.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Save trimmed/cropped video as",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("AVI video", "*.avi"),
                       ("All files", "*.*")],
            initialfile=self._suggest_output_name()
        )
        if not save_path:
            return

        # Compute crop region in video coordinates
        if self.bbox_canvas is not None:
            vx1, vy1, vx2, vy2 = self._bbox_to_video(self.bbox_canvas)
        else:
            vx1, vy1, vx2, vy2 = 0, 0, self.vid_w, self.vid_h

        # Clamp
        vx1 = max(0, min(vx1, self.vid_w - 1))
        vx2 = max(vx1 + 2, min(vx2, self.vid_w))
        vy1 = max(0, min(vy1, self.vid_h - 1))
        vy2 = max(vy1 + 2, min(vy2, self.vid_h))
        crop_w = vx2 - vx1
        crop_h = vy2 - vy1

        # Ensure even dimensions (required by some codecs)
        crop_w = crop_w if crop_w % 2 == 0 else crop_w - 1
        crop_h = crop_h if crop_h % 2 == 0 else crop_h - 1

        self.btn_trim.config(state=tk.DISABLED)
        self.progress["value"] = 0
        self._set_status("Processing…")

        def worker():
            try:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                out = cv2.VideoWriter(save_path, fourcc,
                                      self.fps, (crop_w, crop_h))

                total = self.end_frame - self.start_frame + 1
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_frame)

                for i in range(total):
                    ret, frame = self.cap.read()
                    if not ret:
                        break
                    cropped = frame[vy1:vy1 + crop_h, vx1:vx1 + crop_w]
                    out.write(cropped)

                    # Update progress every ~1%
                    if i % max(1, total // 100) == 0:
                        pct = (i / total) * 100
                        self.root.after(0, self._update_progress, pct)

                out.release()
                self.root.after(0, self._on_done, save_path)

            except Exception as exc:
                self.root.after(0, self._on_error, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, pct: float):
        self.progress["value"] = pct
        self._set_status(f"Processing… {pct:.0f}%")

    def _on_done(self, path: str):
        self.progress["value"] = 100
        self._set_status(f"Saved: {os.path.basename(path)}")
        self.btn_trim.config(state=tk.NORMAL)
        messagebox.showinfo("Done",
                            f"Video saved successfully:\n{path}")

    def _on_error(self, msg: str):
        self.progress["value"] = 0
        self._set_status("Error!")
        self.btn_trim.config(state=tk.NORMAL)
        messagebox.showerror("Error", f"Processing failed:\n{msg}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _normalise_bbox(self, bbox):
        """Ensure x1 < x2 and y1 < y2, clamped to canvas."""
        x1, y1, x2, y2 = bbox
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        x1 = max(0, min(x1, self.canvas_w))
        x2 = max(0, min(x2, self.canvas_w))
        y1 = max(0, min(y1, self.canvas_h))
        y2 = max(0, min(y2, self.canvas_h))
        return x1, y1, x2, y2

    def _bbox_to_video(self, bbox):
        """Convert canvas bbox coords to video pixel coords, respecting zoom/pan."""
        x1, y1, x2, y2 = self._normalise_bbox(bbox)
        vr = self._view_rect()
        vw = vr[2] - vr[0]
        vh = vr[3] - vr[1]
        return (
            int(vr[0] + x1 / self.canvas_w * vw),
            int(vr[1] + y1 / self.canvas_h * vh),
            int(vr[0] + x2 / self.canvas_w * vw),
            int(vr[1] + y2 / self.canvas_h * vh),
        )

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m:02d}:{s:06.3f}"

    def _suggest_output_name(self) -> str:
        base, ext = os.path.splitext(os.path.basename(self.video_path))
        return f"{base}_trimmed{ext}"

    def _set_status(self, msg: str):
        self.lbl_status.config(text=msg)


# ──────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = VideoTrimCropApp(root)    # noqa: F841
    root.mainloop()


if __name__ == "__main__":
    main()
