// Small sounds that make the evidence audible: a pen tick for each item as it is
// marked, a thud when the stamp lands. Synthesised, no files. Silent until the page
// has had a click or a key press, because browsers hold audio back until then.

let context: AudioContext | null = null;
let lastTick = 0;

function audio(): AudioContext | null {
  try {
    if (!context) context = new AudioContext();
    if (context.state === "suspended") void context.resume();
    return context.state === "running" ? context : null;
  } catch {
    return null;
  }
}

/** One item marked. `progress` (0..1) raises the pitch as the count climbs. */
export function tick(progress: number): void {
  const ctx = audio();
  if (!ctx) return;
  const now = ctx.currentTime;
  if (now - lastTick < 0.028) return;  // a burst of items is a purr, not a screech
  lastTick = now;
  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = "triangle";
  oscillator.frequency.setValueAtTime(620 + 520 * progress, now);
  oscillator.frequency.exponentialRampToValueAtTime(380 + 300 * progress, now + 0.05);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.09, now + 0.004);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.07);
  oscillator.connect(gain).connect(ctx.destination);
  oscillator.start(now);
  oscillator.stop(now + 0.08);
}

/** The stamp landing: a low thud with a short burst of noise. A higher chime follows a Verified. */
export function stamp(state: "verified" | "check" | "declined"): void {
  const ctx = audio();
  if (!ctx) return;
  const now = ctx.currentTime;
  const thud = ctx.createOscillator();
  const thudGain = ctx.createGain();
  thud.type = "sine";
  thud.frequency.setValueAtTime(state === "declined" ? 110 : 150, now);
  thud.frequency.exponentialRampToValueAtTime(48, now + 0.16);
  thudGain.gain.setValueAtTime(0.32, now);
  thudGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
  thud.connect(thudGain).connect(ctx.destination);
  thud.start(now);
  thud.stop(now + 0.24);

  const length = Math.floor(ctx.sampleRate * 0.06);
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length);
  const noise = ctx.createBufferSource();
  const noiseGain = ctx.createGain();
  noise.buffer = buffer;
  noiseGain.gain.value = 0.12;
  noise.connect(noiseGain).connect(ctx.destination);
  noise.start(now);

  if (state === "verified") {
    [880, 1318.5].forEach((frequency, i) => {
      const chime = ctx.createOscillator();
      const chimeGain = ctx.createGain();
      const at = now + 0.12 + i * 0.09;
      chime.type = "sine";
      chime.frequency.value = frequency;
      chimeGain.gain.setValueAtTime(0.0001, at);
      chimeGain.gain.exponentialRampToValueAtTime(0.07, at + 0.01);
      chimeGain.gain.exponentialRampToValueAtTime(0.0001, at + 0.5);
      chime.connect(chimeGain).connect(ctx.destination);
      chime.start(at);
      chime.stop(at + 0.55);
    });
  }
}
