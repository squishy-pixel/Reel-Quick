import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Hls from "hls.js";
import type { MediaInfo } from "../api";

export interface PlayerHandle {
  seek(frame: number): void;
  play(rate?: number): void;
  pause(): void;
  toggle(): void;
  shuttle(dir: 1 | -1, slow?: boolean): void;
  /** Continuous rate from the shuttle control: negative plays backwards, 0 holds the frame. */
  setRate(rate: number): void;
  playing(): boolean;
  jog(frames: number): void;
}

export const MAX_RATE = 8;

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
  const [rate, setRateState] = useState(0);
  const reverse = useRef<number | null>(null);
  const [paused, setPaused] = useState(true);
  // Where the app wants the playhead. The video is only believed once it has arrived there: while a seek is in
  // flight it still reports the old position, and when the target lies past the end of a proxy that is still
  // being built, the browser clamps the seek to the last second it has. Neither may drag the playhead away.
  const desired = useRef<number | null>(null);
  const seeking = useRef(false);
  const [beyond, setBeyond] = useState(false);
  const beyondRef = useRef(false);
  const setBeyondBoth = (b: boolean) => { beyondRef.current = b; setBeyond(b); };
  const pos = useRef(0);          // last frame reported to the app (video truth, or the desired frame when beyond)
  const timeOf = (f: number) => off + (f + 0.3) / fps;
  const frameOf = (t: number) => Math.max(0, Math.min(info.nb_frames - 1, Math.floor((t - off) * fps + 1e-4)));
  const seekableEnd = (v: HTMLVideoElement) => {
    try { if (v.seekable.length) return v.seekable.end(v.seekable.length - 1); } catch { /* ignore */ }
    return Number.isFinite(v.duration) ? v.duration : Infinity;
  };
  const reachable = (v: HTMLVideoElement, f: number) => timeOf(f) <= seekableEnd(v) + 0.25;

  const tell = (f: number) => { pos.current = f; onFrameRef.current(f); };
  const stopReverse = () => { if (reverse.current) { cancelAnimationFrame(reverse.current); reverse.current = null; } };
  const goTo = (f: number) => {
    const v = video.current; const clamped = Math.max(0, Math.min(info.nb_frames - 1, f));
    desired.current = clamped;
    tell(clamped);
    if (!v || !proxyUrl) return;
    if (!reachable(v, clamped)) { setBeyondBoth(true); return; }
    setBeyondBoth(false);
    // Before the metadata is in, setting currentTime only queues a start position and no seeked event
    // follows, so the in-flight flag would never clear.
    seeking.current = v.readyState >= 1;
    v.currentTime = timeOf(clamped);
  };
  const jogFrames = (frames: number) => {
    // Trackpad / wheel scrub: pause and move by whole frames, clamped to the recording.
    const v = video.current; if (!v) return;
    if (!v.paused || reverse.current) { stopReverse(); setRateState(0); v.pause(); }
    goTo(Math.round(pos.current + frames));
  };
  const playForward = (r: number) => {
    const v = video.current; if (!v) return; stopReverse();
    if (beyondRef.current) { onErrorRef.current?.("The proxy has not reached this point yet"); setRateState(0); return; }
    desired.current = null;
    v.playbackRate = r; setRateState(r);
    v.play().catch((e: any) => { setRateState(0); onErrorRef.current?.(`Playback refused: ${e?.name ?? "error"} ${e?.message ?? ""}`.trim()); });
  };
  const playReverse = (speed: number) => {
    // HTML video cannot play backwards, so step the time down each animation frame.
    const v = video.current; if (!v) return;
    v.pause();
    setRateState(speed);
    stopReverse();
    desired.current = null;
    setBeyondBoth(false);
    let last = performance.now();
    let t = timeOf(pos.current);
    const tick = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      t = Math.max(0, t + dt * speed);
      // Backing out of a stretch the proxy has not reached yet: count the frames down until the video can
      // show them.
      if (t > seekableEnd(v)) tell(frameOf(t)); else v.currentTime = t;
      if (t <= 0) { setRateState(0); reverse.current = null; return; }
      reverse.current = requestAnimationFrame(tick);
    };
    reverse.current = requestAnimationFrame(tick);
  };
  // Stop, and park the video on the frame the readout shows (they can part while backing through a stretch the
  // proxy has not reached).
  const hold = () => { const v = video.current; stopReverse(); setRateState(0); v?.pause(); goTo(pos.current); };

  useImperativeHandle(ref, () => ({
    seek(f) { goTo(f); },
    play(r = 1) { playForward(r); },
    pause() { hold(); },
    toggle() { const v = video.current; if (!v) return; if (v.paused && !reverse.current) playForward(1); else hold(); },
    jog(frames) { jogFrames(frames); },
    setRate(r) {
      if (!Number.isFinite(r)) return;
      const clamped = Math.max(-MAX_RATE, Math.min(MAX_RATE, r));
      if (clamped === 0) { if (rate !== 0) hold(); return; }
      if (clamped > 0) { if (clamped !== rate) playForward(clamped); return; }
      if (clamped !== rate) playReverse(clamped);
    },
    shuttle(dir, slow = false) {
      if (dir === 1) {
        // L: 1x, 2x, 4x, 8x. Shift+L: quarter speed, then half, then back to 1x.
        const next = slow ? (rate === 0.25 ? 0.5 : rate === 0.5 ? 1 : 0.25) : rate >= 1 ? Math.min(MAX_RATE, rate * 2) : 1;
        playForward(next); return;
      }
      playReverse(slow ? (rate === -0.25 ? -0.5 : rate === -0.5 ? -1 : -0.25) : rate <= -1 ? Math.max(-MAX_RATE, rate * 2) : -1);
    },
    playing() { const v = video.current; return !!v && (!v.paused || reverse.current !== null); },
  }), [fps, info.nb_frames, rate, off, proxyUrl]);  // eslint-disable-line react-hooks/exhaustive-deps

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
    // Once the proxy is far enough along, a playhead parked past its end can be shown by the video after all.
    const onLonger = () => { if (beyondRef.current && desired.current !== null && reachable(v, desired.current)) goTo(desired.current); };
    v.addEventListener("durationchange", onLonger); v.addEventListener("progress", onLonger);
    return () => { v.removeEventListener("durationchange", onLonger); v.removeEventListener("progress", onLonger); if (hls) hls.destroy(); else v.removeAttribute("src"); };
  }, [proxyUrl]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Report the frame under the playhead as precisely as the browser allows.
  useEffect(() => {
    const v = video.current; if (!v) return;
    let handle = 0;
    const anyV = v as HTMLVideoElement & { requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number };
    const report = (t: number) => {
      if (seeking.current && !v.seeking) seeking.current = false;   // the browser finished without telling us
      if (seeking.current || beyondRef.current) return;      // the video is not where the app is
      const f = frameOf(t);
      // While parked on a frame the app asked for, a one-frame disagreement is the seek bias, not news.
      if (desired.current !== null && Math.abs(f - desired.current) <= 1 && v.paused && !reverse.current) return;
      tell(f);
    };
    if (anyV.requestVideoFrameCallback) {
      const loop = (_: number, meta: { mediaTime: number }) => { report(meta.mediaTime); handle = anyV.requestVideoFrameCallback!(loop); };
      handle = anyV.requestVideoFrameCallback(loop);
    }
    const onTime = () => report(v.currentTime);
    const onSeeked = () => {
      seeking.current = false;
      const want = desired.current;
      if (want === null) { report(v.currentTime); return; }
      const f = frameOf(v.currentTime);
      if (Math.abs(f - want) <= 1) { tell(want); return; }
      if (!reachable(v, want)) { setBeyondBoth(true); tell(want); return; }   // clamped to the end of what exists
      tell(f); desired.current = f;                                          // it really is somewhere else
    };
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onSeeked);
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
    return () => { v.removeEventListener("timeupdate", onTime); v.removeEventListener("seeked", onSeeked); v.removeEventListener("play", onPlay); v.removeEventListener("pause", onPause); v.removeEventListener("error", onErr); v.removeEventListener("stalled", onStalled); if (handle && (v as any).cancelVideoFrameCallback) (v as any).cancelVideoFrameCallback(handle); };
  }, [proxyUrl, fps, info.nb_frames, off]);  // eslint-disable-line react-hooks/exhaustive-deps

  const showExact = (exactView && paused && rate === 0) || (beyond && paused);

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
      // Swiping right moves forward, like pushing the footage along. ~6 px per frame feels like a jog
      // wheel; a single tick is capped at two seconds. (A Mac trackpad reports a rightward swipe as a
      // negative deltaX under natural scrolling, hence the sign.)
      const frames = Math.max(-2 * fps, Math.min(2 * fps, Math.trunc(-d / 6)));
      if (frames) jogFrames(frames);
    });
  };
  // The transport bar shows rate and mode; nothing is drawn over the picture.
  useEffect(() => { onStatusRef.current?.({ rate, mode: beyond ? "not in proxy yet" : showExact ? "source frame" : proxyUrl === "/media/source" ? "direct" : proxyUrl ? "proxy" : "no video" }); }, [rate, showExact, proxyUrl, beyond]);

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
