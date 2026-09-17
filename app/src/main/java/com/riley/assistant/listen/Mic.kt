package com.riley.assistant.listen

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.voice.RileyVoice
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.Closeable
import java.io.IOException
import kotlin.math.sqrt

class Utterance(val samples: ShortArray)

/**
 * Continuous 16 kHz microphone capture with a simple loudness-based voice detector.
 * ListenService checks the RECORD_AUDIO permission before creating one.
 */
@SuppressLint("MissingPermission")
class Mic : Closeable {
    private val record: AudioRecord
    private val frame = ShortArray(FRAME_SAMPLES)

    /** The last second of audio before the wake word fired, so "Riley, add a task…" isn't cut off. */
    private val recent = ArrayDeque<ShortArray>()
    private var noiseFloor = 200.0

    init {
        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuffer, FRAME_SAMPLES * 4),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IOException("microphone could not start")
        }
        record.startRecording()
    }

    /**
     * Waits for the wake word. Returns true when "Riley" was heard, false when [interrupt] asked to listen
     * straight away (the MIC button). Audio is ignored while Riley itself is talking.
     */
    suspend fun awaitWake(detector: WakeWordDetector, interrupt: () -> Boolean): Boolean = withContext(Dispatchers.IO) {
        recent.clear()
        detector.reset()
        var heardWakeWord: Boolean? = null
        while (heardWakeWord == null) {
            ensureActive()
            if (KillSwitch.detonated) throw CancellationException("detonated")
            if (interrupt()) {
                heardWakeWord = false
                continue
            }
            val chunk = readFrame()
            if (RileyVoice.speaking.value) {
                detector.reset()
                recent.clear()
                continue
            }
            recent.addLast(chunk)
            while (recent.size > WAKE_TAIL_FRAMES) recent.removeFirst()
            trackNoise(chunk, onlyWhenQuiet = false)
            if (detector.accept(chunk)) heardWakeWord = true
        }
        heardWakeWord == true
    }

    /**
     * Records one spoken phrase and stops after about a second of silence.
     * With [seedFromWake], the audio around the wake word is included and recording has already begun.
     * Returns null if nobody starts speaking within [startTimeoutMs].
     */
    suspend fun recordUtterance(startTimeoutMs: Long, seedFromWake: Boolean): Utterance? = withContext(Dispatchers.IO) {
        val chunks = mutableListOf<ShortArray>()
        val preRoll = ArrayDeque<ShortArray>()
        var started = false
        var speechMs = 0L
        var silenceMs = 0L
        var totalMs = 0L
        var waitedMs = 0L
        if (seedFromWake && recent.isNotEmpty()) {
            chunks.addAll(recent)
            started = true
            speechMs = MIN_SPEECH_MS
            totalMs = recent.size * FRAME_MS
        }
        recent.clear()

        var result: Utterance? = null
        var finished = false
        while (!finished) {
            ensureActive()
            if (KillSwitch.detonated) throw CancellationException("detonated")
            val chunk = readFrame()
            val loud = rms(chunk) > threshold()

            if (!started) {
                if (loud) {
                    started = true
                    chunks.addAll(preRoll)
                    preRoll.clear()
                    chunks.add(chunk)
                    speechMs = FRAME_MS
                    silenceMs = 0
                    totalMs = FRAME_MS
                } else {
                    trackNoise(chunk, onlyWhenQuiet = true)
                    preRoll.addLast(chunk)
                    if (preRoll.size > PRE_ROLL_FRAMES) preRoll.removeFirst()
                    waitedMs += FRAME_MS
                    if (waitedMs >= startTimeoutMs) finished = true
                }
                continue
            }

            chunks.add(chunk)
            totalMs += FRAME_MS
            if (loud) {
                speechMs += FRAME_MS
                silenceMs = 0
            } else {
                silenceMs += FRAME_MS
            }
            if (silenceMs >= END_SILENCE_MS || totalMs >= MAX_UTTERANCE_MS) {
                if (speechMs >= MIN_SPEECH_MS) {
                    result = Utterance(flatten(chunks))
                    finished = true
                } else {
                    // Just a click or a cough: keep waiting for real speech.
                    waitedMs += totalMs
                    started = false
                    chunks.clear()
                    speechMs = 0
                    silenceMs = 0
                    totalMs = 0
                    if (waitedMs >= startTimeoutMs) finished = true
                }
            }
        }
        result
    }

    /** Throws away audio captured while Riley was thinking or talking. */
    fun drain() {
        val scratch = ShortArray(FRAME_SAMPLES)
        while (record.read(scratch, 0, FRAME_SAMPLES, AudioRecord.READ_NON_BLOCKING) > 0) {
            // discard
        }
        recent.clear()
    }

    override fun close() {
        runCatching { record.stop() }
        record.release()
    }

    private fun readFrame(): ShortArray {
        var read = 0
        while (read < FRAME_SAMPLES) {
            val n = record.read(frame, read, FRAME_SAMPLES - read)
            if (n < 0) throw IOException("microphone read failed ($n)")
            read += n
        }
        return frame.copyOf()
    }

    private fun threshold(): Double = maxOf(noiseFloor * 3.0, MIN_THRESHOLD)

    private fun trackNoise(chunk: ShortArray, onlyWhenQuiet: Boolean) {
        val level = rms(chunk)
        if (onlyWhenQuiet && level > threshold()) return
        noiseFloor = (noiseFloor * 0.97 + level * 0.03).coerceIn(50.0, 4000.0)
    }

    private fun rms(chunk: ShortArray): Double {
        var sum = 0.0
        for (sample in chunk) sum += sample.toDouble() * sample
        return sqrt(sum / chunk.size)
    }

    private fun flatten(chunks: List<ShortArray>): ShortArray {
        val out = ShortArray(chunks.sumOf { it.size })
        var offset = 0
        for (chunk in chunks) {
            System.arraycopy(chunk, 0, out, offset, chunk.size)
            offset += chunk.size
        }
        return out
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val FRAME_SAMPLES = 1_600 // 100 ms
        private const val FRAME_MS = 100L
        private const val WAKE_TAIL_FRAMES = 10
        private const val PRE_ROLL_FRAMES = 3
        private const val END_SILENCE_MS = 1_100L
        private const val MAX_UTTERANCE_MS = 20_000L
        private const val MIN_SPEECH_MS = 300L
        private const val MIN_THRESHOLD = 400.0
    }
}
