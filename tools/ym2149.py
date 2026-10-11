"""Minimal YM2149 (Atari ST PSG, 2 MHz) renderer driven by a timed register-write log."""
import array
import wave

CLOCK = 2_000_000
# Normalised output level for 4-bit volumes (YM2149 is logarithmic, ~3 dB per step).
VOLUME = [0.0, 0.0078, 0.0110, 0.0156, 0.0221, 0.0313, 0.0442, 0.0625,
          0.0884, 0.1250, 0.1768, 0.2500, 0.3536, 0.5000, 0.7071, 1.0]


class YM2149:
    def __init__(self, rate: int = 44100):
        self.rate = rate
        self.r = [0] * 16
        self.tone_phase = [0.0] * 3
        self.tone_out = [1] * 3
        self.noise_phase = 0.0
        self.lfsr = 1
        self.noise_out = 1
        self.env_phase = 0.0
        self.env_step = 0
        self.env_hold = False

    def write(self, reg: int, value: int) -> None:
        reg &= 15
        self.r[reg] = value & 0xFF
        if reg == 13:
            self.env_step, self.env_phase, self.env_hold = 0, 0.0, False

    def _env_level(self) -> float:
        shape, s = self.r[13] & 15, self.env_step     # s counts 0..31 per ramp (32 steps)
        attack = bool(shape & 4)
        if shape < 8:                                 # single ramp then 0
            if s >= 32:
                return 0.0
            v = s if attack else 31 - s
        else:
            cont, alt, hold = True, bool(shape & 2), bool(shape & 1)
            cycle, pos = divmod(s, 32)
            if hold and cycle >= 1:
                v = (31 if attack != alt else 0)
            else:
                up = attack != (alt and cycle % 2 == 1)
                v = pos if up else 31 - pos
        return VOLUME[v // 2] if v > 0 else 0.0

    def render(self, seconds: float) -> list:
        out = []
        n = int(seconds * self.rate)
        r = self.r
        for _ in range(n):
            for ch in range(3):
                period = (r[ch * 2] | (r[ch * 2 + 1] & 15) << 8) or 1
                self.tone_phase[ch] += CLOCK / (16 * period) / self.rate * 2
                while self.tone_phase[ch] >= 1:
                    self.tone_phase[ch] -= 1
                    self.tone_out[ch] ^= 1
            np = (r[6] & 31) or 1
            self.noise_phase += CLOCK / (16 * np) / self.rate
            while self.noise_phase >= 1:
                self.noise_phase -= 1
                bit = (self.lfsr ^ (self.lfsr >> 3)) & 1
                self.lfsr = (self.lfsr >> 1) | (bit << 16)
                self.noise_out = self.lfsr & 1
            ep = (r[11] | r[12] << 8) or 1
            self.env_phase += CLOCK / (8 * ep) / self.rate
            while self.env_phase >= 1:
                self.env_phase -= 1
                self.env_step = min(self.env_step + 1, 1 << 20)
            env = None
            mix = 0.0
            for ch in range(3):
                tone_on = not (r[7] >> ch) & 1
                noise_on = not (r[7] >> (ch + 3)) & 1
                gate = (self.tone_out[ch] or not tone_on) and (self.noise_out or not noise_on)
                if not gate:
                    continue
                vol = r[8 + ch]
                if vol & 16:
                    env = self._env_level() if env is None else env
                    mix += env
                else:
                    mix += VOLUME[vol & 15]
            out.append(mix / 3)
        return out


def render_log(log: list, duration: float, rate: int = 44100) -> list:
    """log: (time_seconds, reg, value) sorted by time."""
    ym, samples, t, i = YM2149(rate), [], 0.0, 0
    step = 1 / 200                                    # 5 ms render slices
    while t < duration:
        while i < len(log) and log[i][0] <= t:
            ym.write(log[i][1], log[i][2])
            i += 1
        nxt = min(t + step, log[i][0] if i < len(log) else duration, duration)
        nxt = max(nxt, t + 1 / rate)
        samples += ym.render(nxt - t)
        t = nxt
    return samples


def write_wav(path, samples: list, rate: int = 44100) -> None:
    peak = max(1e-9, max(abs(s - 0.5 * 0) for s in samples)) if samples else 1
    mean = sum(samples) / len(samples) if samples else 0
    pcm = array.array("h", (int(max(-1, min(1, (s - mean) * 1.6)) * 32000) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
