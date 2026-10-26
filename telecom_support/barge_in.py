"""Local speech detection with playback-reference rejection and microphone pre-roll.

Reference subtraction rejects direct speaker echo; it is not room acoustic echo
cancellation. A headset remains the reliable choice for simultaneous speech.
"""
from collections import deque

import numpy as np
import webrtcvad


class BargeInDetector:
    def __init__(self, *, vad=None):
        self.vad = vad or webrtcvad.Vad(2)
        self.reference = np.zeros(0, dtype=np.float64)
        self.reset()

    def reset(self):
        self.pending = b""
        self.preroll = deque(maxlen=8)  # 512 ms at the microphone's 1024 frames.
        self.voiced = 0
        self.reference = np.zeros(0, dtype=np.float64)

    def playback(self, data):
        samples = np.frombuffer(data, dtype="<i2").astype(np.float64)
        # The output is 24 kHz, microphone and VAD are 16 kHz.
        samples = np.interp(np.arange(0, len(samples), 1.5), np.arange(len(samples)), samples)
        self.reference = np.concatenate((self.reference, samples))[-16000:]

    def residual(self, samples):
        ref = self.reference
        n = len(samples)
        energy = np.dot(samples, samples)
        if len(ref) < n or energy < 1:
            return samples
        correlation = np.correlate(ref, samples, mode="valid")
        sums = np.concatenate(([0.], np.cumsum(ref * ref)))
        powers = sums[n:] - sums[:-n]
        score = np.abs(correlation) / np.sqrt(np.maximum(powers * energy, 1))
        best = int(np.argmax(score))
        if score[best] < .60:
            return samples
        matched = ref[best:best + n]
        gain = correlation[best] / max(powers[best], 1)
        return samples - gain * matched

    def feed(self, data):
        self.preroll.append(data)
        self.pending += data
        while len(self.pending) >= 640:  # 20 ms, signed 16-bit mono.
            frame, self.pending = self.pending[:640], self.pending[640:]
            samples = self.residual(np.frombuffer(frame, dtype="<i2").astype(np.float64))
            pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
            voiced = np.sqrt(np.mean(samples * samples)) >= 180 and self.vad.is_speech(pcm, 16000)
            self.voiced = self.voiced + 1 if voiced else 0
            if self.voiced >= 6:  # 120 ms sustained speech, not a single click.
                result = b"".join(self.preroll)
                self.reset()
                return result
        return None
