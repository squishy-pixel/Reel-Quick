import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Hls from "hls.js";
import type { MediaInfo } from "../api";

export interface PlayerHandle {
  seek(frame: number): void;
  play(rate?: number): void;
  pause(): void;
  toggle(): void;
  shuttle(dir: 1 | -1, slow?: boolean): void;
  playing(): boolean;
  jog(frames: number): void;
}

export const Player = forwardRef<PlayerHandle, {
  info: MediaInfo; proxyUrl: string | null; frame: number; onFrame: (f: number) => void; exactView: boolean;
  onStatus?: (s: { rate: number; mode: string }) => void;
  onError?: (message: string) => void;
  startOffset?: number;      // seconds between the proxy's zero and its first video frame (AAC priming)
}>(function Player({ info, proxyUrl, frame, onFrame, exactView, onStatus, onError, startOffset = 0 }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const fps = info.fps;
  const off = startOffset;
  // Callbacks live in refs so a parent re-render never tears the media pipeline down.
  const onErrorRef = useRef(onError); onErrorRef.current = onError;
  const onFrameRef = useRef(onFrame); onFrameRef.current = onFrame;
  const onStatusRef = useRef(onStatus); onStatusRef.current = onStatus;
  const [rate, setRate] = useState(0);
  const reverse = useRef<number | null>(null);
  const [paused, setPaused] = useState(true);

  const stopReverse = () => { if (reverse.current) { cancelAnimationFrame(reverse.current); reverse.current = null; } };
  const jogFrames = (frames: number) => {
    // Trackpad / wheel scrub: pause and move by whole frames, clamped to the recording.
    const v = video.current; if (!v) return;
    if (!v.paused || reverse.current) { stopReverse(); setRate(0); v.pause(); }
    const target = Math.max(0, Math.min(info.nb_frames - 1, Math.round(((v.currentTime - off) * fps) + frames)));
    onFrameRef.current(target);
    v.currentTime = off + (target + 0.3) / fps;
  };

  useImperativeHandle(ref, () => ({
    seek(f) {
      const v = video.current; const clamped = Math.max(0, Math.min(info.nb_frames - 1, f));
      onFrameRef.current(clamped);
      if (v) { v.currentTime = off + (clamped + 0.3) / fps; }
    },
    play(r = 1) {
      const v = video.current; if (!v) return; stopReverse(); v.playbackRate = r; setRate(r);
      v.play().catch((e: any) => { setRate(0); onErrorRef.current?.(`Playback refused: ${e?.name ?? "error"} ${e?.message ?? ""}`.trim()); });
    },
    pause() { const v = video.current; stopReverse(); setRate(0); v?.pause(); },
    toggle() { const v = video.current; if (!v) return; if (v.paused && !reverse.current) { this.play(1); } else { this.pause(); } },
    jog(frames) { jogFrames(frames); },
    shuttle(dir, slow = false) {
      const v = video.current; if (!v) return;
      if (dir === 1) {
        // L: 1x, 2x, 4x, 8x. Shift+L: quarter speed, then half, then back to 1x.
        const next = slow ? (rate === 0.25 ? 0.5 : rate === 0.5 ? 1 : 0.25) : rate >= 1 ? Math.min(8, rate * 2) : 1;
        this.play(next); return;
      }
      // Reverse: HTML video cannot play backwards, so step the time down each animation frame.
      v.pause();
      const speed = slow ? (rate === -0.25 ? -0.5 : rate === -0.5 ? -1 : -0.25) : rate <= -1 ? Math.max(-8, rate * 2) : -1;
      setRate(speed);
      stopReverse();
      let last = performance.now();
      const tick = (now: number) => {
        const dt = (now - last) / 1000; last = now;
        v.currentTime = Math.max(0, v.currentTime + dt * speed);
        if (v.currentTime <= 0) { setRate(0); reverse.current = null; return; }
        reverse.current = requestAnimationFrame(tick);
      };
      reverse.current = requestAnimationFrame(tick);
    },
    playing() { const v = video.current; return !!v && (!v.paused || reverse.current !== null); },
  }), [fps, info.nb_frames, rate, off]);

  // Attach the source: a playlist through hls.js where the browser lacks native HLS (Chromium), natively
  // where it has it (Safari, the Mac app window); a plain MP4 goes straight in.
  useEffect(() => {
    const v = video.current; if (!v || !proxyUrl) return;
    let hls: Hls | null = null;
    // Safari (and the Mac app window, which is WebKit) gets its own built-in HLS player: it is the
    // reference implementation and handles a growing playlist well. Everything else gets hls.js.
    // canPlayType alone is not trusted: Chromium answers "maybe" for HLS and then plays nothing.
    const ua = navigator.userAgent;
    const isSafari = /safari/i.test(ua) && !/chrome|chromium|crios|edg|opr|android/i.test(ua);
    if (proxyUrl.endsWith(".m3u8") && !isSafari && Hls.isSupported()) {
      hls = new Hls({ startPosition: 0, liveDurationInfinity: false, maxBufferLength: 20, backBufferLength: 30 });
      hls.loadSource(proxyUrl);
      hls.attachMedia(v);
      hls.on(Hls.Events.ERROR, (_e, data) => { if (data.fatal) onErrorRef.current?.(`Proxy playback error: ${data.type} ${data.details}`); });
    } else {
      v.src = proxyUrl;
    }
    return () => { if (hls) hls.destroy(); else v.removeAttribute("src"); };
  }, [proxyUrl]);

  // Report the frame under the playhead as precisely as the browser allows.
  useEffect(() => {
    const v = video.current; if (!v) return;
    let handle = 0;
    const anyV = v as HTMLVideoElement & { requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number };
    const report = (t: number) => onFrameRef.current(Math.max(0, Math.min(info.nb_frames - 1, Math.floor((t - off) * fps + 1e-4))));
    if (anyV.requestVideoFrameCallback) {
      const loop = (_: number, meta: { mediaTime: number }) => { report(meta.mediaTime); handle = anyV.requestVideoFrameCallback!(loop); };
      handle = anyV.requestVideoFrameCallback(loop);
    }
    const onTime = () => report(v.currentTime);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onTime);
    const onPlay = () => setPaused(false), onPause = () => setPaused(true);
    v.addEventListener("play", onPlay); v.addEventListener("pause", onPause);
    const onErr = () => {
      const codes: Record<number, string> = { 1: "aborted", 2: "network error", 3: "decode error", 4: "source not supported" };
      const err = v.error;
      onErrorRef.current?.(`Video ${err ? codes[err.code] ?? `error ${err.code}` : "error"}${err?.message ? `: ${err.message}` : ""} (${proxyUrl})`);
    };
    v.addEventListener("error", onErr);
    const onStalled = () => onErrorRef.current?.("Video stalled waiting for data from the proxy");
    v.addEventListener("stalled", onStalled);
    return () => { v.removeEventListener("timeupdate", onTime); v.removeEventListener("seeked", onTime); v.removeEventListener("play", onPlay); v.removeEventListener("pause", onPause); v.removeEventListener("error", onErr); v.removeEventListener("stalled", onStalled); if (handle && (v as any).cancelVideoFrameCallback) (v as any).cancelVideoFrameCallback(handle); };
  }, [proxyUrl, fps, info.nb_frames, off]);

  const showExact = exactView && paused && rate === 0;

  // Two-finger horizontal swipe over the picture jogs frames: a slow swipe steps single frames, a flick
  // covers seconds, and the trackpad's own inertia gives the glide. Shift+vertical wheel does the same
  // for a mouse. Vertical scrolling alone is left alone.
  const wheelAcc = useRef(0);
  const wheelRaf = useRef<number | null>(null);
  const onWheel = (e: React.WheelEvent) => {
    const horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
    const delta = horizontal ? e.deltaX : e.shiftKey ? e.deltaY : 0;
    if (!delta) return;
    e.preventDefault();
    wheelAcc.current += delta;
    if (wheelRaf.current) return;
    wheelRaf.current = requestAnimationFrame(() => {
      wheelRaf.current = null;
      const d = wheelAcc.current; wheelAcc.current = 0;
      // ~6 px per frame feels like a jog wheel; cap a single tick at two seconds
      const frames = Math.max(-2 * fps, Math.min(2 * fps, Math.trunc(d / 6)));
      if (frames) jogFrames(frames);
    });
  };
  // The transport bar shows rate and mode; nothing is drawn over the picture.
  useEffect(() => { onStatusRef.current?.({ rate, mode: showExact ? "source frame" : proxyUrl ? "proxy" : "no video" }); }, [rate, showExact, proxyUrl]);

  return (
    <div className="player">
      <div className="stage" onWheel={onWheel}>
        {proxyUrl ? (
          <video ref={video} preload="auto" playsInline style={{ display: showExact ? "none" : "block" }} />
        ) : !showExact ? (
          <div className="nothing hatch" style={{ padding: "40px 60px" }}>
            <div className="caps accent">No proxy yet</div>
            <div>Build the proxy to scrub with video, or press <span className="kbd">F</span> to view exact source frames while you step.</div>
          </div>
        ) : null}
        {showExact && <img className="frame" src={`/api/frame/${frame}?width=1280`} alt="" />}
      </div>
    </div>
  );
});

export function startFrames(info: MediaInfo): number {
  // frames since midnight of the first frame's timecode; mirrors MediaInfo.start_frames
  const tc = info.start_timecode; if (!tc) return 0;
  const m = /^(\d{2}):(\d{2}):(\d{2})[:;.](\d{2})$/.exec(tc); if (!m) return 0;
  const n = Math.round(info.fps);
  const [hh, mm, ss, ff] = m.slice(1).map(Number);
  let total = n * 3600 * hh + n * 60 * mm + n * ss + ff;
  if (info.drop_frame) { const d = 2 * Math.floor(n / 30); const mins = 60 * hh + mm; total -= d * (mins - Math.floor(mins / 10)); }
  return total;
}
