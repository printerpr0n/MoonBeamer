import asyncio
import json
import logging
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple


class RTMPStreamer:
    """
    Single-session RTMP streaming:
      - One "main" ffmpeg publishes to RTMP for the entire session
      - One "feeder" ffmpeg produces the live camera+music+overlay segment into a FIFO (mpegts)
      - main concatenates: [intro?] + [fifo-live] + [outro?] in one RTMP session
      - On stop: terminate feeder => FIFO EOF => main proceeds to outro (if enabled) then exits
    """

    BUILD_ID = "rtmp_streamer_single_session_v2_fifo_fix"

    def __init__(self, config):
        self.server = config.get_server()
        self.logger = logging.getLogger("rtmp_streamer")

        # --- Required config ---
        self.ffmpeg_path = config.get("ffmpeg_path", "/usr/bin/ffmpeg")
        self.ffprobe_path = config.get("ffprobe_path", "/usr/bin/ffprobe")
        self.webcam_url = config.get("webcam_url")
        self.rtmp_url = config.get("rtmp_url")
        if not self.webcam_url or not self.rtmp_url:
            raise config.error("webcam_url and rtmp_url are required")

        # --- Optional media ---
        self.bg_music_file = config.get("bg_music_file", None)

        self.intro_video = config.get("intro_video", None)
        self.outro_video = config.get("outro_video", None)
        self.intro_enabled = config.getboolean("intro_enabled", False)
        self.outro_enabled = config.getboolean("outro_enabled", False)
        self.intro_duration = config.getfloat("intro_duration", 0.0)
        self.outro_duration = config.getfloat("outro_duration", 0.0)

        # --- Behavior ---
        self.enabled = config.getboolean("enabled", True)
        self.autostart = config.getboolean("autostart", True)
        self.autostop = config.getboolean("autostop", True)
        self.stop_delay = config.getfloat("stop_delay", 30.0)
        self.poll_interval = config.getfloat("poll_interval", 3.0)

        # --- Encoding targets ---
        self.video_bitrate = config.get("video_bitrate", "2500k")
        self.audio_bitrate = config.get("audio_bitrate", "128k")
        self.preset = config.get("preset", "veryfast")
        self.fps = config.getint("fps", 30)
        self.resolution = config.get("resolution", "").strip()
        self.target_width, self.target_height = self._parse_resolution(self.resolution)
        self.audio_rate = config.getint("audio_rate", 44100)

        # --- Overlay (applied on LIVE segment by feeder) ---
        self.overlay_enabled = config.getboolean("overlay_enabled", True)
        self.overlay_text = config.get("overlay_text", "Printing")
        self.overlay_font = config.get(
            "overlay_font",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )

        # --- Cache dir ---
        cache_dir = config.get("media_cache_dir", "/tmp/rtmp_streamer_cache")
        self.media_cache_dir = Path(cache_dir).expanduser()
        self.media_cache_dir.mkdir(parents=True, exist_ok=True)

        # --- ffmpeg stderr log ---
        self.ffmpeg_log_path = Path(
            config.get("ffmpeg_log_path", "/tmp/rtmp_streamer_ffmpeg.log")
        ).expanduser()

        # FIFO for live segment
        self.fifo_path = self.media_cache_dir / "live_segment.ts"

        # Runtime state
        self._main_proc: Optional[subprocess.Popen] = None
        self._feeder_proc: Optional[subprocess.Popen] = None
        self._stream_start_time: Optional[float] = None
        self._stopping = False
        self._pending_stop_task: Optional[asyncio.Task] = None
        self._current_print_state = "unknown"

        # Locks
        self._stream_lock = asyncio.Lock()
        self._media_lock = asyncio.Lock()

        # Klippy API
        self.klippy_apis = self.server.lookup_component("klippy_apis")

        # HTTP endpoint
        self.server.register_endpoint("/server/rtmp_streamer", ["GET", "POST"], self._handle_http)

        # Remote methods (portable)
        self._register_remote_methods_portable()

        # Background tasks
        asyncio.get_event_loop().create_task(self._prepare_media_background())
        asyncio.get_event_loop().create_task(self._monitor_print_loop())

        self.logger.warning(
            "RTMPStreamer loaded (%s). enabled=%s autostart=%s autostop=%s fifo=%s ffmpeg_log=%s",
            self.BUILD_ID, self.enabled, self.autostart, self.autostop,
            str(self.fifo_path), str(self.ffmpeg_log_path)
        )

    # ---------------------------------------------------------------------
    # Remote methods (portable)
    # ---------------------------------------------------------------------

    def _register_remote_methods_portable(self):
        names = [
            ("server.get_rtmp_streamer", self._rm_get),
            ("get_rtmp_streamer", self._rm_get),
            ("server.post_rtmp_streamer", self._rm_post),
            ("post_rtmp_streamer", self._rm_post),
        ]
        registered = []

        reg = getattr(self.server, "register_remote_method", None)
        if callable(reg):
            for n, h in names:
                try:
                    reg(n, h)
                    registered.append(f"server:{n}")
                except Exception as e:
                    self.logger.warning("Remote method register failed (%s): %s", n, e)

        # Optional webhooks if present (never required)
        webhooks = None
        for comp_name in ("webhooks", "klippy_webhooks"):
            try:
                webhooks = self.server.lookup_component(comp_name)
                break
            except Exception:
                pass
        if webhooks is not None:
            wh_reg = getattr(webhooks, "register_remote_method", None)
            if callable(wh_reg):
                for n, h in names:
                    try:
                        wh_reg(n, h)
                        registered.append(f"webhooks:{n}")
                    except Exception as e:
                        self.logger.warning("Webhooks remote method register failed (%s): %s", n, e)

        self.logger.warning("RTMPStreamer (%s) remote methods registered: %s",
                            self.BUILD_ID, registered if registered else "(none)")

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    @staticmethod
    def _parse_resolution(res: str) -> Tuple[int, int]:
        if not res:
            return (0, 0)
        try:
            w_s, h_s = res.lower().split("x", 1)
            return (int(w_s.strip()), int(h_s.strip()))
        except Exception:
            return (0, 0)

    @staticmethod
    def _file_exists(path: Optional[str]) -> bool:
        if not path:
            return False
        try:
            return Path(path).expanduser().is_file()
        except Exception:
            return False

    @staticmethod
    def _parse_fps(r_frame_rate: str) -> float:
        try:
            if "/" in r_frame_rate:
                a, b = r_frame_rate.split("/", 1)
                return float(a) / float(b)
            return float(r_frame_rate)
        except Exception:
            return 0.0

    @staticmethod
    def _escape_drawtext_text(s: str) -> str:
        return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def _target_profile(self) -> dict:
        return {
            "v_codec": "h264",
            "pix_fmt": "yuv420p",
            "width": self.target_width,
            "height": self.target_height,
            "fps": float(self.fps),
            "a_codec": "aac",
            "a_rate": int(self.audio_rate),
        }

    def is_streaming(self) -> bool:
        return self._main_proc is not None and self._main_proc.poll() is None

    def feeder_running(self) -> bool:
        return self._feeder_proc is not None and self._feeder_proc.poll() is None

    def stream_uptime(self) -> float:
        return 0.0 if not self._stream_start_time else (time.time() - self._stream_start_time)

    # ---------------------------------------------------------------------
    # ffprobe utilities
    # ---------------------------------------------------------------------

    async def _ffprobe_json(self, path: str) -> dict:
        if not self._file_exists(self.ffprobe_path) or not self._file_exists(path):
            return {}
        proc = await asyncio.create_subprocess_exec(
            self.ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0 or not out:
            return {}
        try:
            return json.loads(out.decode("utf-8", errors="ignore"))
        except Exception:
            return {}

    async def _get_media_info(self, path: str) -> dict:
        data = await self._ffprobe_json(path)
        if not data:
            return {}
        info = {
            "v_codec": "",
            "pix_fmt": "",
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "has_audio": False,
            "a_codec": "",
            "a_rate": 0,
            "duration": 0.0,
        }
        fmt = data.get("format") or {}
        dur = fmt.get("duration")
        try:
            info["duration"] = float(dur) if dur is not None else 0.0
        except Exception:
            info["duration"] = 0.0

        for st in (data.get("streams") or []):
            if st.get("codec_type") == "video" and not info["v_codec"]:
                info["v_codec"] = st.get("codec_name") or ""
                info["pix_fmt"] = st.get("pix_fmt") or ""
                info["width"] = int(st.get("width") or 0)
                info["height"] = int(st.get("height") or 0)
                info["fps"] = self._parse_fps(st.get("r_frame_rate") or "")
            if st.get("codec_type") == "audio" and not info["has_audio"]:
                info["has_audio"] = True
                info["a_codec"] = st.get("codec_name") or ""
                try:
                    info["a_rate"] = int(st.get("sample_rate") or 0)
                except Exception:
                    info["a_rate"] = 0
        return info

    # ---------------------------------------------------------------------
    # Normalization (intro/outro)
    # ---------------------------------------------------------------------

    def _needs_normalize(self, info: dict, target: dict) -> bool:
        if not info:
            return True
        if not info.get("has_audio", False):
            return True
        if info.get("v_codec") != target["v_codec"]:
            return True
        if info.get("pix_fmt") != target["pix_fmt"]:
            return True
        if target["width"] and target["height"]:
            if info.get("width") != target["width"] or info.get("height") != target["height"]:
                return True
        if target["fps"] > 0 and abs((info.get("fps") or 0.0) - target["fps"]) > 0.2:
            return True
        if info.get("a_codec") != target["a_codec"]:
            return True
        if info.get("a_rate") and info["a_rate"] != target["a_rate"]:
            return True
        return False

    def _normalized_path(self, src: str, role: str) -> Path:
        p = Path(src).expanduser()
        try:
            mtime = int(p.stat().st_mtime)
        except Exception:
            mtime = 0
        w = self.target_width or 0
        h = self.target_height or 0
        return self.media_cache_dir / f"{role}_{p.stem}_{w}x{h}_{self.fps}fps_{mtime}.mp4"

    async def ensure_normalized(self, src: Optional[str], role: str) -> Optional[str]:
        if not src or not self._file_exists(src):
            return None

        target = self._target_profile()
        info = await self._get_media_info(src)

        if not self._needs_normalize(info, target):
            return src

        out_path = self._normalized_path(src, role)
        if out_path.is_file():
            return str(out_path)

        has_audio = bool(info.get("has_audio", False))

        cmd = [self.ffmpeg_path, "-y", "-i", str(Path(src).expanduser())]
        if not has_audio:
            cmd += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={self.audio_rate}"]

        vf = None
        if self.target_width and self.target_height:
            vf = (
                f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease,"
                f"pad={self.target_width}:{self.target_height}:(ow-iw)/2:(oh-ih)/2"
            )

        cmd += [
            "-c:v", "libx264",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-g", str(self.fps * 2),
            "-keyint_min", str(self.fps * 2),
            "-sc_threshold", "0",
            "-preset", self.preset,
            "-b:v", self.video_bitrate,
        ]
        if vf:
            cmd += ["-vf", vf]

        cmd += ["-c:a", "aac", "-b:a", self.audio_bitrate, "-ar", str(self.audio_rate)]
        if not has_audio:
            cmd += ["-shortest"]

        cmd += ["-movflags", "+faststart", str(out_path)]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            msg = (err.decode("utf-8", errors="ignore") if err else "").strip()
            self.logger.warning("Normalization failed for %s. ffmpeg stderr tail:\n%s", role, msg[-2000:])
            return None

        return str(out_path)

    async def prepare_media(self) -> dict:
        async with self._media_lock:
            intro_norm = await self.ensure_normalized(self.intro_video, "intro")
            outro_norm = await self.ensure_normalized(self.outro_video, "outro")
            if intro_norm:
                self.intro_video = intro_norm
            if outro_norm:
                self.outro_video = outro_norm
            return {"intro_video": self.intro_video, "outro_video": self.outro_video, "cache_dir": str(self.media_cache_dir)}

    async def _prepare_media_background(self):
        try:
            await self.prepare_media()
        except Exception as e:
            self.logger.warning("Background media preparation failed: %s", e)

    # ---------------------------------------------------------------------
    # FIFO management
    # ---------------------------------------------------------------------

    def _is_fifo(self, path: Path) -> bool:
        try:
            st = os.stat(str(path))
            return stat.S_ISFIFO(st.st_mode)
        except Exception:
            return False

    def _recreate_fifo(self):
        # Always remove whatever is there (file OR fifo) then create a fifo fresh
        try:
            if self.fifo_path.exists():
                self.fifo_path.unlink()
        except Exception:
            pass

        os.mkfifo(self.fifo_path, 0o666)

    # ---------------------------------------------------------------------
    # Process spawn + logging
    # ---------------------------------------------------------------------

    def _spawn_ffmpeg(self, cmd: list, tag: str) -> subprocess.Popen:
        self.ffmpeg_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.ffmpeg_log_path, "ab", buffering=0)
        header = f"\n\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} [{tag}] =====\nCMD: {' '.join(cmd)}\n".encode()
        try:
            log_f.write(header)
        except Exception:
            pass
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_f, stdin=subprocess.DEVNULL, close_fds=True)

    async def _check_fast_exit(self, proc: subprocess.Popen, tag: str):
        await asyncio.sleep(1.0)
        rc = proc.poll()
        if rc is not None and rc != 0:
            self.logger.warning("ffmpeg exited immediately (tag=%s rc=%s). See %s", tag, rc, str(self.ffmpeg_log_path))

    # ---------------------------------------------------------------------
    # Feeder and Main
    # ---------------------------------------------------------------------

    def _build_feeder_command(self) -> list:
        # IMPORTANT: include -y so it never prompts "Overwrite? [y/N]"
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel", "info",
            "-rw_timeout", "5000000",
            "-re",
            "-i", self.webcam_url,
        ]

        if self.bg_music_file:
            cmd += ["-stream_loop", "-1", "-i", self.bg_music_file]
            cmd += ["-map", "0:v:0", "-map", "1:a:0?"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]

        vf_parts = []
        if self.target_width and self.target_height:
            vf_parts.append(
                f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease,"
                f"pad={self.target_width}:{self.target_height}:(ow-iw)/2:(oh-ih)/2"
            )
        if self.overlay_enabled:
            safe_text = self._escape_drawtext_text(self.overlay_text)
            vf_parts.append(
                f"drawtext=fontfile={self.overlay_font}:text='{safe_text}':"
                "x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
            )
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]

        cmd += [
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-preset", self.preset,
            "-b:v", self.video_bitrate,
            "-pix_fmt", "yuv420p",
            "-g", str(self.fps * 2),
            "-keyint_min", str(self.fps * 2),
            "-sc_threshold", "0",
            "-c:a", "aac",
            "-b:a", self.audio_bitrate,
            "-ar", str(self.audio_rate),
            "-f", "mpegts",
            str(self.fifo_path),
        ]
        return cmd

    def _build_main_command(self, use_intro: bool, use_outro: bool) -> list:
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "info",
            "-rw_timeout", "5000000",
        ]

        inputs = []
        segments = []

        if use_intro:
            inputs.append(self.intro_video)
            segments.append(0)

        inputs.append(str(self.fifo_path))
        fifo_index = len(inputs) - 1
        segments.append(fifo_index)

        if use_outro:
            inputs.append(self.outro_video)
            segments.append(len(inputs) - 1)

        for i, inp in enumerate(inputs):
            if i == fifo_index:
                cmd += ["-i", inp]
            else:
                cmd += ["-re", "-i", inp]

        parts = []
        for idx in segments:
            parts.append(f"[{idx}:v:0]")
            parts.append(f"[{idx}:a:0]")

        n = len(segments)
        filter_complex = "".join(parts) + f"concat=n={n}:v=1:a=1[v][a]"

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-preset", self.preset,
            "-b:v", self.video_bitrate,
            "-pix_fmt", "yuv420p",
            "-g", str(self.fps * 2),
            "-keyint_min", str(self.fps * 2),
            "-sc_threshold", "0",
            "-r", str(self.fps),
            "-c:a", "aac",
            "-b:a", self.audio_bitrate,
            "-ar", str(self.audio_rate),
            "-f", "flv",
            self.rtmp_url
        ]
        return cmd

    # ---------------------------------------------------------------------
    # Start/Stop
    # ---------------------------------------------------------------------

    async def start_stream(self):
        async with self._stream_lock:
            self.logger.warning("start_stream() called (%s)", self.BUILD_ID)
            if not self.enabled or self.is_streaming() or self._stopping:
                return

            await self.prepare_media()

            use_intro = self.intro_enabled and self._file_exists(self.intro_video)
            use_outro = self.outro_enabled and self._file_exists(self.outro_video)

            # Recreate FIFO fresh (prevents "already exists" overwrite prompt)
            try:
                self._recreate_fifo()
            except Exception as e:
                self.logger.warning("Failed to create FIFO at %s: %s", str(self.fifo_path), e)
                return

            # Start main first (it will block until feeder writes)
            main_cmd = self._build_main_command(use_intro=use_intro, use_outro=use_outro)
            self._main_proc = self._spawn_ffmpeg(main_cmd, "main_single_session")
            self._stream_start_time = time.time()
            asyncio.get_event_loop().create_task(self._check_fast_exit(self._main_proc, "main"))

            # Start feeder to open FIFO for writing
            feeder_cmd = self._build_feeder_command()
            self._feeder_proc = self._spawn_ffmpeg(feeder_cmd, "feeder_live_to_fifo")
            asyncio.get_event_loop().create_task(self._check_fast_exit(self._feeder_proc, "feeder"))

    async def stop_stream(self):
        async with self._stream_lock:
            self.logger.warning("stop_stream() called (%s)", self.BUILD_ID)
            if self._stopping:
                return
            self._stopping = True

            # Stop feeder FIRST so FIFO EOF => main proceeds to outro then exits
            if self._feeder_proc and self._feeder_proc.poll() is None:
                try:
                    self._feeder_proc.terminate()
                except Exception:
                    pass
                for _ in range(10):
                    if self._feeder_proc.poll() is not None:
                        break
                    await asyncio.sleep(0.3)
                if self._feeder_proc.poll() is None:
                    try:
                        self._feeder_proc.kill()
                    except Exception:
                        pass
            self._feeder_proc = None

            # Wait for main to finish (outro plays here if enabled)
            if self._main_proc:
                wait_seconds = 180
                start = time.time()
                while self._main_proc.poll() is None and (time.time() - start) < wait_seconds:
                    await asyncio.sleep(0.5)
                if self._main_proc.poll() is None:
                    try:
                        self._main_proc.terminate()
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                    if self._main_proc.poll() is None:
                        try:
                            self._main_proc.kill()
                        except Exception:
                            pass
            self._main_proc = None
            self._stream_start_time = None

            # Cleanup FIFO
            try:
                if self.fifo_path.exists():
                    self.fifo_path.unlink()
            except Exception:
                pass

            self._stopping = False

    # ---------------------------------------------------------------------
    # Print monitoring
    # ---------------------------------------------------------------------

    async def _monitor_print_loop(self):
        await self._wait_for_klippy_ready()
        last_state = None
        while True:
            state = await self._get_print_state()
            if state != last_state:
                await self._on_print_state_change(last_state, state)
                last_state = state
                self._current_print_state = state
            await asyncio.sleep(self.poll_interval)

    async def _wait_for_klippy_ready(self):
        while self.server.get_klippy_info().get("state") != "ready":
            await asyncio.sleep(1)

    async def _get_print_state(self):
        try:
            r = await self.klippy_apis.query_objects({"print_stats": None})
            return r["print_stats"]["state"]
        except Exception:
            return "unknown"

    async def _on_print_state_change(self, old, new):
        if new == "printing" and old != "printing":
            if self._pending_stop_task:
                self._pending_stop_task.cancel()
                self._pending_stop_task = None
            if self.autostart:
                await self.start_stream()

        if old == "printing" and new in ("complete", "error", "standby"):
            if self.autostop:
                self._pending_stop_task = asyncio.create_task(self._delayed_stop())

    async def _delayed_stop(self):
        try:
            await asyncio.sleep(self.stop_delay)
            if self._current_print_state != "printing":
                await self.stop_stream()
        except asyncio.CancelledError:
            pass

    # ---------------------------------------------------------------------
    # Status + HTTP + Remote methods
    # ---------------------------------------------------------------------

    def _status_dict(self):
        return {
            "build_id": self.BUILD_ID,
            "enabled": self.enabled,
            "streaming": self.is_streaming(),
            "feeder_running": self.feeder_running(),
            "uptime": round(self.stream_uptime(), 1),
            "intro_enabled": self.intro_enabled,
            "outro_enabled": self.outro_enabled,
            "intro_video": self.intro_video,
            "outro_video": self.outro_video,
            "target": self._target_profile(),
            "cache_dir": str(self.media_cache_dir),
            "fifo_path": str(self.fifo_path),
            "ffmpeg_log_path": str(self.ffmpeg_log_path),
        }

    async def _dispatch_op(self, op: str):
        op = (op or "").strip()
        if op == "start":
            await self.start_stream()
            return {"ok": True, **self._status_dict()}
        if op == "stop":
            await self.stop_stream()
            return {"ok": True, **self._status_dict()}
        if op == "enable":
            self.enabled = True
            return {"ok": True, **self._status_dict()}
        if op == "disable":
            self.enabled = False
            return {"ok": True, **self._status_dict()}
        if op == "intro_enable":
            self.intro_enabled = True
            return {"ok": True, **self._status_dict()}
        if op == "intro_disable":
            self.intro_enabled = False
            return {"ok": True, **self._status_dict()}
        if op == "outro_enable":
            self.outro_enabled = True
            return {"ok": True, **self._status_dict()}
        if op == "outro_disable":
            self.outro_enabled = False
            return {"ok": True, **self._status_dict()}
        if op == "prepare_media":
            res = await self.prepare_media()
            return {"ok": True, **res, **self._status_dict()}
        raise self.server.error(f"Invalid operation: {op}")

    async def _handle_http(self, web_request):
        if web_request.get_action() == "GET":
            return self._status_dict()
        op = web_request.get_str("op", "")
        return await self._dispatch_op(op)

    async def _rm_get(self, **kwargs):
        self.logger.warning("remote get called (%s) kwargs=%s", self.BUILD_ID, kwargs)
        return self._status_dict()

    async def _rm_post(self, **kwargs):
        self.logger.warning("remote post called (%s) kwargs=%s", self.BUILD_ID, kwargs)
        op = str(kwargs.get("op", "") or "")
        return await self._dispatch_op(op)


def load_component(config):
    return RTMPStreamer(config)
