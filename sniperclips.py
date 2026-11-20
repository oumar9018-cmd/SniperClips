#!/usr/bin/env python3
"""
SniperClips — link do, ready clips lo.

v2 upgrades over v1:
  1. FAST ENERGY MAP — one WAV extract + pure-Python RMS per 1s (seconds, not
     hundreds of ffmpeg calls).
  2. SENTENCE-SNAPPED CUTS — clips start/end on whisper segment boundaries and
     pauses, never mid-sentence (complete thought).
  3. HOOK STRUCTURE — the highest-hype sentence inside the window is moved to
     the front of the clip (<=1.5s lead-in) and burned as a big title for 3s.
  4. REFRAME FIX — 9:16 blur-pad (blurred bg + sharp fg), works on 720p.
  5. COPYRIGHT PRE-CHECK — per-clip speech-density vs energy analysis gives a
     GREEN/YELLOW/RED music-risk flag. Risk levels only, NEVER a guarantee.
  6. TRANSPARENT SCORING — clips/REPORT.md lists the exact reasons per clip.
  7. clips/index.html — phone-friendly gallery of the finished clips.

Usage:
  venv/bin/python clipforge.py <video-url-or-file> [--clips 5] [--model small]
"""
import argparse, array, html, json, math, os, statistics, subprocess, sys
import time, wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "clips")
os.makedirs(OUT, exist_ok=True)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TIMINGS = {}

# Hype signals — English + Hinglish gaming/stream vocabulary
HYPE_WORDS = {
    "insane", "crazy", "unbelievable", "impossible", "clutch", "legendary",
    "headshot", "kill", "kills", "win", "winner", "epic", "wow", "amazing",
    "bro", "bhai", "pagal", "khatarnak", "arre", "arey", "dekh", "dekho",
    "maar", "jeet", "jeeta", "tabahi", "zabardast", "bawal", "dhamaka",
    "kamaal", "shandaar", "maza", "danger", "khatra", "finally", "wait",
    "ruko", "suno", "shock", "hairan", "yakeen", "op", "gg", "pro",
    "sniper", "blast", "trailer", "rockstar", "gta", "god", "omg",
}
HYPE_PHRASES = [
    "oh my god", "no way", "let's go", "lets go", "ho gaya", "kya baat",
    "maza aa gaya", "next level", "mind blowing", "aa gaya", "dekh lo",
    "believe nahi", "yakeen nahi", "kya hai ye", "what is this",
]
QUESTION_STARTS = ("why", "what", "how", "kyun", "kya", "kaise", "kab",
                   "which", "when", "kon", "kaun")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def stage(name):
    TIMINGS[name] = time.time()
    print(f"[{name}] shuru...", flush=True)


def stage_done(name):
    dt = time.time() - TIMINGS.pop(name, time.time())
    print(f"[{name}] khatam — {dt:.1f}s", flush=True)
    return dt


def download(url):
    ytdlp = os.path.join(HERE, "venv", "bin", "yt-dlp")
    if not os.path.exists(ytdlp):
        ytdlp = "yt-dlp"
    out = os.path.join(OUT, "source.%(ext)s")
    r = run([ytdlp, "-f", "bv*[height<=1080]+ba/b[height<=1080]",
             "--merge-output-format", "mp4", "-o", out, url])
    if r.returncode != 0:
        sys.exit("Download fail:\n" + r.stderr[-800:])
    for f in os.listdir(OUT):
        if f.startswith("source."):
            return os.path.join(OUT, f)
    sys.exit("Download ke baad file nahi mili")


# ---------------------------------------------------------------- energy map
def energy_map(path):
    """Extract 16kHz mono WAV once; RMS dB per 1-second window, pure Python."""
    wav_path = os.path.join(HERE, "_work_audio.wav")
    r = run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
             "-ac", "1", "-ar", "16000", wav_path])
    if r.returncode != 0:
        sys.exit("WAV extract fail: " + r.stderr[-400:])
    energies = []
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        total = w.getnframes()
        sec = 0
        while sec * rate < total:
            frames = w.readframes(rate)
            if not frames:
                break
            samples = array.array("h")
            samples.frombytes(frames)
            if samples:
                mean_sq = sum(s * s for s in samples) / len(samples)
                rms = math.sqrt(mean_sq)
                db = 20.0 * math.log10(rms / 32768.0) if rms > 0 else -70.0
                energies.append(round(max(db, -70.0), 2))
            sec += 1
    return energies


# --------------------------------------------------------------- transcription
def transcribe(path, model_name):
    # faster-whisper 1.2.1 calls av.open(..., metadata_errors="ignore") but
    # newer PyAV removed that kwarg -> TypeError. Drop it instead of pinning.
    import av
    _orig_open = av.open

    def _patched_open(*a, **kw):
        kw.pop("metadata_errors", None)
        return _orig_open(*a, **kw)

    av.open = _patched_open
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(path, word_timestamps=True,
                                       vad_filter=True)
    segs = []
    for seg in segments:
        words = [{"start": w.start, "end": w.end, "word": w.word.strip()}
                 for w in (seg.words or []) if w.word.strip()]
        segs.append({"start": seg.start, "end": seg.end,
                     "text": seg.text.strip(), "words": words})
    return segs


def load_transcript(src, model_name):
    cache = src + f".transcript.{model_name}.json"
    if os.path.exists(cache):
        print(f"Transcript cache se load: {cache}", flush=True)
        return json.load(open(cache))
    segs = transcribe(src, model_name)
    json.dump(segs, open(cache, "w"))
    print(f"Transcript cache save: {cache}", flush=True)
    return segs


# ------------------------------------------------------------------- scoring
def hype_in_text(text):
    """Return (count, hits) of hype words/phrases in a sentence."""
    low = " " + text.lower() + " "
    hits = []
    count = 0
    for ph in HYPE_PHRASES:
        c = low.count(ph)
        if c:
            count += 2 * c
            hits.append(ph)
    for tok in low.replace(",", " ").replace(".", " ").replace("!", " ") \
                  .replace("?", " ").split():
        if tok in HYPE_WORDS:
            count += 1
            hits.append(tok)
    return count, hits


def annotate_sentences(segs):
    for s in segs:
        c, hits = hype_in_text(s["text"])
        s["hype"] = c
        s["hype_hits"] = hits
        s["nwords"] = len(s["words"]) or len(s["text"].split())
    return segs


def score_windows(segs, energies):
    """Candidate windows anchored on sentence starts, snapped to sentence
    boundaries (complete thought). Returns scored window dicts."""
    if not segs:
        return []
    med = statistics.median(energies) if energies else -40.0
    peak_ref = sorted(energies)[int(len(energies) * 0.95)] if energies else -20.0
    windows = []
    n = len(segs)
    for i in range(n):
        start = segs[i]["start"]
        j = i
        while j < n and segs[j]["end"] - start < 30.0:
            j += 1
        if j >= n:
            continue
        end = segs[j]["end"]
        dur = end - start
        if dur > 72.0:
            continue
        win_segs = segs[i:j + 1]
        hype_total = sum(s["hype"] for s in win_segs)
        if hype_total == 0:
            continue
        hits = []
        for s in win_segs:
            hits.extend(s["hype_hits"])
        nwords = sum(s["nwords"] for s in win_segs)
        pace = nwords / dur
        ewin = energies[int(start):int(end) + 1] or [med]
        epeak = max(ewin)
        enorm = max(0.0, (epeak - med) / max(1.0, peak_ref - med)) * 3.0
        quest = 0
        for s in win_segs:
            toks = s["text"].lower().split()
            if toks and toks[0].strip("?,!.") in QUESTION_STARTS:
                quest += 1
            if "?" in s["text"]:
                quest += 1
        score = hype_total * 1.5 + enorm + min(pace, 4.0) + quest * 0.7
        windows.append({
            "start": start, "end": end, "dur": dur, "seg_i": i, "seg_j": j,
            "score": round(score, 2), "hype_total": hype_total,
            "hype_hits": hits, "energy_peak": epeak, "pace": round(pace, 2),
            "questions": quest, "nwords": nwords,
        })
    windows.sort(key=lambda w: -w["score"])
    return windows


def pick_top(windows, count):
    picked = []
    for w in windows:
        overlap = any(not (w["end"] <= p["start"] + 3 or
                           w["start"] >= p["end"] - 3) for p in picked)
        if not overlap:
            picked.append(w)
        if len(picked) >= count:
            break
    return picked


def apply_hook(win, segs):
    """Move the window so the highest-hype sentence opens the clip."""
    win_segs = segs[win["seg_i"]:win["seg_j"] + 1]
    hook = max(win_segs, key=lambda s: (s["hype"], s["nwords"]))
    start, end = win["start"], win["end"]
    if hook["start"] - start > 5.0:
        prev = segs[win["seg_i"]]
        candidate = hook["start"] - 1.2
        if prev["start"] <= candidate < prev["end"] and \
                hook["start"] - prev["start"] <= 6.5:
            start = prev["start"]          # full-sentence lead-in
        else:
            start = max(start, hook["start"] - 0.4)
        # Cap end at the last sentence end <= start+70 ...
        cap_end = None
        idx = win["seg_i"]
        while idx < len(segs) and segs[idx]["end"] <= start + 70.0:
            cap_end = segs[idx]["end"]
            idx += 1
        if cap_end is not None:
            end = min(end, cap_end)
        # ... then extend the end forward to restore >=30s where possible
        k = win["seg_j"]
        while end - start < 30.0 and k + 1 < len(segs) and \
                segs[k + 1]["end"] <= start + 70.0:
            k += 1
            end = segs[k]["end"]
        if end - start < 25.0:
            # End of video — duration cannot be restored; keep the
            # original complete-thought window instead of a stub clip.
            start, end = win["start"], win["end"]
    title_words = hook["text"].split()[:7]
    title = " ".join(title_words)
    if len(hook["text"].split()) > 7:
        title += "…"
    return {"start": round(start, 2), "end": round(end, 2),
            "dur": round(end - start, 2), "hook": hook, "title": title,
            "score": win["score"], "hype_hits": win["hype_hits"],
            "energy_peak": win["energy_peak"], "pace": win["pace"],
            "questions": win["questions"]}


# ---------------------------------------------------------- copyright pre-check
def clip_risk(clip, words, energies, med_energy):
    """Speech density vs energy: loud + speechless stretches = likely music."""
    start, end = clip["start"], clip["end"]
    total_bins = max(1, int(end) - int(start))
    nonspeech_run, longest = 0, 0
    nonspeech = 0
    for t in range(int(start), int(end)):
        has_speech = any(w["start"] < t + 1 and w["end"] > t
                         for w in words)
        loud = t < len(energies) and energies[t] > med_energy
        if loud and not has_speech:
            nonspeech += 1
            nonspeech_run += 1
            longest = max(longest, nonspeech_run)
        else:
            nonspeech_run = 0
    frac = nonspeech / total_bins
    if frac > 0.55 or longest > 20:
        risk = "RED"
    elif longest > 6 or frac >= 0.25:
        risk = "YELLOW"
    else:
        risk = "GREEN"
    return {"risk": risk, "music_suspect_longest_s": longest,
            "nonspeech_frac": round(frac, 2)}


# ------------------------------------------------------------------ rendering
def make_srt(words, start, end, path):
    sel = [w for w in words if start <= w["start"] < end]
    lines, i = [], 1

    def ts(x):
        x = max(0.0, x - start)
        h, m = int(x // 3600), int((x % 3600) // 60)
        s, ms = int(x % 60), int((x - int(x)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for j in range(0, len(sel), 4):  # 4 words per caption chunk
        chunk = sel[j:j + 4]
        if not chunk:
            continue
        cend = max(chunk[-1]["end"], chunk[0]["start"] + 0.45)
        lines.append(f"{i}\n{ts(chunk[0]['start'])} --> {ts(cend)}\n"
                     + " ".join(w["word"] for w in chunk) + "\n")
        i += 1
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return len(sel)


def cut_clip(src, clip, idx, words):
    srt = os.path.join(OUT, f"clip{idx}.srt")
    n = make_srt(words, clip["start"], clip["end"], srt)
    hookfile = os.path.join(OUT, f"clip{idx}.hook.txt")
    # Wrap the hook title to <=2 lines of <=18 chars and shrink the font
    # for long titles — a single line at 64px overflows a 1080-wide frame.
    words_t = clip["title"].replace("\n", " ").split()
    lines_t, cur = [], ""
    for w in words_t:
        if len(cur) + len(w) + 1 <= 18:
            cur = (cur + " " + w).strip()
        else:
            lines_t.append(cur)
            cur = w
    if cur:
        lines_t.append(cur)
    lines_t = lines_t[:2]
    longest = max((len(x) for x in lines_t), default=10)
    fontsize = max(36, min(64, int(1150 / max(longest, 10))))
    with open(hookfile, "w") as f:
        f.write("\n".join(lines_t))
    out = os.path.join(OUT, f"clip{idx}.mp4")
    dur = clip["end"] - clip["start"]
    graph = (
        "[0:v]split=2[bgin][fgin];"
        "[bgin]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=42[bgs];"
        "[fgin]scale=1080:-2[fgs];"
        "[bgs][fgs]overlay=(W-w)/2:(H-h)/2[comp];"
        f"[comp]subtitles='{srt}':force_style='FontSize=22,Bold=1,"
        "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=3,"
        "Alignment=2,MarginV=100'[subbed];"
        f"[subbed]drawtext=fontfile='{FONT}':textfile='{hookfile}':"
        f"fontsize={fontsize}:fontcolor=white:borderw=4:bordercolor=black:"
        "x=(w-text_w)/2:y=110:line_spacing=8:"
        "enable='between(t,0,3)'[vout]"
    )
    r = run(["ffmpeg", "-y", "-v", "error", "-ss", str(clip["start"]),
             "-t", str(dur), "-i", src, "-filter_complex", graph,
             "-map", "[vout]", "-map", "0:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
             "-c:a", "aac", "-b:a", "128k", out])
    if r.returncode != 0:
        print(f"Clip {idx} fail:", r.stderr[-500:], flush=True)
        return None
    clip["srt_words"] = n
    return out


# ------------------------------------------------------------------- reports
def mmss(x):
    return f"{int(x // 60)}:{int(x % 60):02d}"


def write_reports(clips_meta, src_name, timings, model_name):
    rep = ["# SniperClips — Clip Report", "",
           f"Source: `{src_name}` · Model: {model_name} · "
           f"Clips: {len(clips_meta)}", "",
           "Har clip ke peeche koi jaadu number nahi hai — neeche wahi "
           "wajahen hain jin par ye chuna gaya.", ""]
    cop = ["# SniperClips — Copyright Risk Report", "",
           "Ye sirf ek **risk andaza** hai, copyright ki guarantee NAHI. "
           "Content ID YouTube ka apna band database hai — koi bahari tool "
           "pakka nahi bata sakta ke claim aayega ya nahi.", "",
           "Risk is baat par hai: jahan awaaz tez hai par boli (speech) "
           "nahi hai, wahan aksar background music hota hai — aur claims "
           "sabse zyada wahi se aate hain.", ""]
    cards = []
    badge = {"GREEN": "#1db954", "YELLOW": "#e6b800", "RED": "#e02424"}
    for i, c in enumerate(clips_meta, 1):
        hits = ", ".join(sorted(set(c["hype_hits"]))[:12]) or "—"
        rep += [f"## Clip {i} — {mmss(c['start'])} → {mmss(c['end'])} "
                f"({c['dur']:.0f}s)",
                f"- Hook line (clip is se shuru hota hai): “{c['title']}”",
                f"- Hype words mile: {hits}",
                f"- Sabse tez awaaz (energy peak): {c['energy_peak']} dB",
                f"- Boli ki raftaar: {c['pace']} words/sec",
                f"- Sawal wale pal: {c['questions']}",
                f"- Combined score (ranking ke liye, virality ka dawa nahi): "
                f"{c['score']}",
                f"- Copyright risk: **{c['risk']['risk']}**", ""]
        cop += [f"## Clip {i} — risk: {c['risk']['risk']}",
                f"- Bina-boli tez-awaaz wala sabse lamba hissa: "
                f"{c['risk']['music_suspect_longest_s']}s "
                f"(music ka shak yahin hota hai)",
                f"- Poori clip me aise hisse: "
                f"{int(c['risk']['nonspeech_frac'] * 100)}%", ""]
        cards.append(f"""
    <div class="card">
      <video controls playsinline preload="metadata" src="clip{i}.mp4"></video>
      <div class="body">
        <div class="hook">{html.escape(c['title'])}</div>
        <div class="meta">{mmss(c['start'])} → {mmss(c['end'])} ·
          {c['dur']:.0f}s · energy {c['energy_peak']} dB ·
          {c['pace']} words/s</div>
        <div class="why">Kyun chuna: hype — {html.escape(hits)}</div>
        <span class="badge" style="background:{badge[c['risk']['risk']]}">
          Copyright risk: {c['risk']['risk']}</span>
      </div>
    </div>""")
    cop += ["## Saaf salah", "",
            "- Sirf wahi content clip karo jiska haq ya ijaazat tumhare paas "
            "hai — apni streams, ya creator ki khuli ijaazat wali.",
            "- Background music claims ki sabse badi wajah hai — YELLOW/RED "
            "clip dalne se pehle music wala hissa kaat do ya badal do.",
            "- Sirf captions + vertical crop ko YouTube 'naya kaam' nahi "
            "maanta — reused-content policy se pure channel ki kamai band "
            "ho sakti hai. Apni commentary/awaaz jodo.",
            "- Dalne ke baad YouTube Studio ke 'Checks' ka result zaroor "
            "dekho — wahi asal pehchan hai.", ""]
    with open(os.path.join(OUT, "REPORT.md"), "w") as f:
        f.write("\n".join(rep))
    with open(os.path.join(OUT, "COPYRIGHT-REPORT.md"), "w") as f:
        f.write("\n".join(cop))
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SniperClips — Ready Clips</title>
<style>
  body {{ background:#0b0b0f; color:#f2f2f2; font-family:system-ui,sans-serif;
         margin:0; padding:16px; }}
  h1 {{ font-size:22px; margin:4px 0 2px; }}
  .sub {{ color:#aaa; font-size:13px; margin-bottom:16px; }}
  .wrap {{ max-width:480px; margin:0 auto; }}
  .card {{ background:#16161d; border-radius:14px; overflow:hidden;
          margin-bottom:20px; }}
  video {{ width:100%; aspect-ratio:9/16; background:#000; display:block; }}
  .body {{ padding:12px 14px 14px; }}
  .hook {{ font-size:17px; font-weight:700; line-height:1.3; }}
  .meta {{ color:#bbb; font-size:12.5px; margin-top:5px; }}
  .why {{ color:#ddd; font-size:13px; margin-top:7px; }}
  .badge {{ display:inline-block; margin-top:10px; padding:4px 10px;
           border-radius:999px; color:#fff; font-size:12px;
           font-weight:700; }}
</style></head><body><div class="wrap">
  <h1>ClipForge — Ready Clips</h1>
  <div class="sub">Source: {html.escape(src_name)} · {len(clips_meta)} clips ·
    risk = andaza hai, guarantee nahi</div>
  {''.join(cards)}
</div></body></html>"""
    with open(os.path.join(OUT, "index.html"), "w") as f:
        f.write(page)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--model", default="small")
    a = ap.parse_args()
    t0 = time.time()
    timings = {}

    stage("source")
    src = a.source if os.path.exists(a.source) else download(a.source)
    timings["source"] = stage_done("source")
    print("Source:", src, flush=True)

    stage("energy")
    energies = energy_map(src)
    timings["energy"] = stage_done("energy")
    print(f"Energy bins: {len(energies)}s", flush=True)

    stage("transcribe")
    segs = load_transcript(src, a.model)
    timings["transcribe"] = stage_done("transcribe")
    words = [w for s in segs for w in s["words"]]
    print(f"Sentences: {len(segs)} · Words: {len(words)}", flush=True)

    stage("score")
    segs = annotate_sentences(segs)
    windows = score_windows(segs, energies)
    picked = pick_top(windows, a.clips)
    clips = [apply_hook(w, segs) for w in picked]
    timings["score"] = stage_done("score")
    print(f"Windows: {len(windows)} · Chune: {len(clips)}", flush=True)

    med_energy = statistics.median(energies) if energies else -40.0
    stage("cut")
    done, meta = [], []
    for i, c in enumerate(clips, 1):
        c["risk"] = clip_risk(c, words, energies, med_energy)
        print(f"Clip {i}: {c['start']:.0f}-{c['end']:.0f}s "
              f"score {c['score']} risk {c['risk']['risk']} "
              f"— {c['title']}", flush=True)
        p = cut_clip(src, c, i, words)
        if p:
            done.append(p)
            meta.append(c)
    timings["cut"] = stage_done("cut")

    write_reports(meta, os.path.basename(src), timings, a.model)
    total = time.time() - t0
    print("TIMINGS:", json.dumps({k: round(v, 1) for k, v in timings.items()}),
          f"total {total:.1f}s", flush=True)
    print("READY:", json.dumps(done, indent=1), flush=True)


if __name__ == "__main__":
    main()
