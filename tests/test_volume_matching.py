"""Volume matching, end to end: level track → bridge level mode → room → microphone.

Uses the real bridge code (Zone._level_chunk, apply_gain) and the real page
code (analyseLevel in index.html, run with Node).

    python3 tests/test_volume_matching.py      # needs numpy and node
"""

import sys, types, json, wave, numpy as np
import pathlib, subprocess, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = str(ROOT / "airplay2_sendspin_bridge" / "app")
TMP = pathlib.Path(tempfile.mkdtemp())


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        return _Any


stub(
    "aiohttp",
    ClientError=Exception,
    ClientSession=object,
    ClientTimeout=object,
    web=stub("aiohttp.web"),
)
for n in [
    "aiosendspin",
    "aiosendspin.audio",
    "aiosendspin.models",
    "aiosendspin.noise.pairing",
    "aiosendspin.server",
]:
    stub(n)
stub("aiosendspin.audio.format", AudioFormat=_Any)
stub(
    "aiosendspin.models.types",
    ConnectionReason=_Any,
    PairMethod=_Any,
    PlayerCommand=_Any,
)
stub(
    "aiosendspin.noise",
    FileServerPairingStore=_Any,
    Identity=_Any,
    b64url_decode=_Any,
    decode_psk_token=_Any,
)
sys.modules["aiosendspin.noise.pairing"].PairingAttempt = _Any
sys.modules["aiosendspin.server"].SendspinServer = _Any
sys.path.insert(0, APP)
import web  # noqa: E402

(TMP / "level-noise.wav").write_bytes(web.build_level_wav())
import bridge  # noqa: E402


def room_ir(sr, rt60, rng, direct_delay_ms):
    n = int(sr * rt60 * 1.2)
    t = np.arange(n) / sr
    h = rng.standard_normal(n) * np.exp(-6.9 * t / rt60) * 0.15
    h[: int(sr * 0.003)] = 0
    d = int(sr * direct_delay_ms / 1000)
    g = np.zeros(n + d)
    g[d] = 1.0
    g[d:] += h
    return g


def simulate(
    true_db,
    sr_mic=48000,
    offset_ms=0,
    rt60=0.5,
    noise_db=-60,
    airplay_db=-6,
    trim_db=0.0,
    seed=0,
    start_s=2.3,
    zone_hp=None,
):
    rng = np.random.default_rng(seed)
    w = wave.open(str(TMP / "level-noise.wav"))
    track = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64)
    track = track[int(start_s * 48000) : int((start_s + 40) * 48000)] * 10 ** (
        airplay_db / 20
    )
    pcm_in = np.round(track).astype("<i2")
    # bridge: stereo S16 chunks of 960 frames
    z = types.SimpleNamespace(_lvl_buf=[], _lvl_silent=0)
    out = []
    for i in range(0, len(pcm_in) - 960, 960):
        mono = pcm_in[i : i + 960]
        st = np.repeat(mono, 2).astype("<i2").tobytes()
        o = bridge.Zone._level_chunk(z, st)
        if trim_db < 0:
            o = bridge.apply_gain(o, 10 ** (trim_db / 20))
        out.append(np.frombuffer(o, "<i2")[::2])
    zone_sig = np.concatenate(out).astype(np.float64)
    ref_sig = track[: len(zone_sig)].copy()
    ref_sig[np.abs(ref_sig) < 0] = 0
    sr = 48000
    zone_sig = np.concatenate([np.zeros(max(0, int(sr * offset_ms / 1000))), zone_sig])[
        : len(ref_sig)
    ]
    if zone_hp:  # echo: less bass
        from numpy.fft import rfft, irfft, rfftfreq

        F = rfft(zone_sig)
        f = rfftfreq(len(zone_sig), 1 / sr)
        F *= 1 / np.sqrt(1 + (zone_hp / np.maximum(f, 1)) ** 4)
        zone_sig = irfft(F, len(zone_sig))
    hr = room_ir(sr, rt60, rng, 6)
    hz = room_ir(sr, rt60, rng, 7)
    mic = (
        np.convolve(ref_sig, hr)[: len(ref_sig)]
        + np.convolve(zone_sig * 10 ** (true_db / 20), hz)[: len(ref_sig)]
    )
    mic = mic / 32768 * 0.3
    mic += rng.standard_normal(len(mic)) * 10 ** (noise_db / 20)
    if sr_mic != sr:
        t = np.arange(int(len(mic) * sr_mic / sr)) / sr_mic
        mic = np.interp(t, np.arange(len(mic)) / sr, mic)
    return mic.astype(np.float32), sr_mic


cases = [
    dict(true_db=-8.0),
    dict(true_db=+3.5, sr_mic=44100),
    dict(true_db=0.0, rt60=0.8),
    dict(true_db=-15.0, offset_ms=120),
    dict(true_db=+6.0, offset_ms=-150, sr_mic=44100),
    dict(true_db=-3.0, trim_db=-4.0, zone_hp=180),
    dict(true_db=+1.0, noise_db=-45, airplay_db=-15),
    dict(true_db=-4.0, offset_ms=450),
    dict(true_db=-100.0),  # zone silent → must be refused
]
meta = []
for i, c in enumerate(cases):
    mic, sr = simulate(seed=i, start_s=1.0 + 0.77 * i, **c)
    mic.tofile(TMP / f"m{i}.f32")
    meta.append(dict(i=i, sr=sr, **c))
(TMP / "cases.json").write_text(json.dumps(meta))
JS = r"""
const fs=require('fs'), dir=process.argv[1];
const h=fs.readFileSync(process.argv[2],'utf8');
const js=h.split('<script>')[1].split('</script>')[0];
const a=js.indexOf('const LV_CYCLE'), b=js.indexOf('async function measureLevel');
eval(js.slice(a,b).replace(/\\blet\\b/g,'var').replace(/\\bconst\\b/g,'var'));
let bad=0;
for (const c of JSON.parse(fs.readFileSync(dir+'/cases.json'))) {
  const buf=fs.readFileSync(dir+`/m${c.i}.f32`);
  const x=new Float32Array(buf.buffer,buf.byteOffset,buf.length/4);
  const from=Math.round(8*c.sr), r=analyseLevel(x,c.sr,from,from+Math.round(20*c.sr));
  const exp=c.true_db+(c.trim_db||0);
  const pass = exp < -60 ? !r.ok : (r.ok && Math.abs(r.diff-exp) <= 0.8);
  if (!pass) bad++;
  console.log(`expect ${exp.toFixed(1)} dB → ${r.ok ? r.diff.toFixed(2)+' dB' : 'refused'}  snr ${r.snr.toFixed(0)}  ${pass?'OK':'FAIL'}`);
}
process.exit(bad?1:0);
"""
html = ROOT / "airplay2_sendspin_bridge" / "app" / "static" / "index.html"
sys.exit(subprocess.run(["node", "-e", JS, str(TMP), str(html)]).returncode)
