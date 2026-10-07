// Sounds as the game plays them (mapping exported by tools/export_audio.py from cso_jesterhead):
// an ability's clip starts with its animation (VisualPlayerAgent.PlayAnimationSFX), the death clip with Die(),
// the revive clip with Revive(), and a stomp for each successful tap of a "tap rapidly" interaction.
export class Sound {
  constructor(base, defs) {
    this.base = base; this.defs = defs;
    this.ctx = null; this.buffers = {}; this.sources = []; this.token = 0;
    this.enabled = true; this.rate = 1; this.volume = 0.05;
    this.master = null;
  }

  context() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain(); this.master.gain.value = this.volume;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
    return this.ctx;
  }

  buffer(name) {                                     // a failed download is forgotten, so the next play retries it
    const ctx = this.context();
    return this.buffers[name] ||= fetch(`${this.base}${name}.mp3`)
      .then(r => { if (!r.ok) throw new Error(`${name}.mp3: HTTP ${r.status}`); return r.arrayBuffer(); })
      .then(b => ctx.decodeAudioData(b))
      .catch(err => { delete this.buffers[name]; throw err; });
  }

  async play(def) {
    if (!this.enabled || !def) return;
    const token = this.token, ctx = this.context();
    const buf = await this.buffer(def.clip).catch(() => null);
    if (!buf || token !== this.token || !this.enabled) return;     // stopped or switched while loading
    const src = ctx.createBufferSource(), gain = ctx.createGain();
    src.buffer = buf; src.playbackRate.value = this.rate; gain.gain.value = def.volume ?? 1;
    src.connect(gain).connect(this.master);
    src.start(ctx.currentTime + (def.delay || 0) / this.rate);
    src.onended = () => { this.sources = this.sources.filter(s => s !== src); };
    this.sources.push(src);
  }

  state(name) { return this.play(this.defs.states[name]); }
  tap(i = 0) { return this.play(this.defs.taps[Math.min(i, this.defs.taps.length - 1)]); }

  stop() {
    this.token++;
    for (const s of this.sources) { try { s.stop(); } catch (e) { /* already stopped */ } }
    this.sources = [];
  }

  setVolume(v) {
    this.volume = v;
    if (this.master) this.master.gain.value = v;
  }

  setRate(r) { this.rate = r; for (const s of this.sources) s.playbackRate.value = r; }
  pause(p) { if (this.ctx) (p ? this.ctx.suspend() : this.ctx.resume()); }
  setEnabled(on) { this.enabled = on; if (!on) this.stop(); }
}
