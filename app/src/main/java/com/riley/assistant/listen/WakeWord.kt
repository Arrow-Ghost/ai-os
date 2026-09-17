package com.riley.assistant.listen

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import java.io.Closeable
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

sealed interface ModelStatus {
    data object NotInstalled : ModelStatus
    data class Downloading(val percent: Int) : ModelStatus
    data object Installed : ModelStatus
    data class Failed(val message: String) : ModelStatus
}

/** The offline Vosk speech model used only to hear the wake word. Downloaded once (about 36 MB). */
object WakeModel {
    private const val URL_ZIP = "https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip"
    private const val DIR_NAME = "vosk-model"
    private const val READY_MARKER = "READY"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null

    private val _status = MutableStateFlow<ModelStatus>(ModelStatus.NotInstalled)
    val status: StateFlow<ModelStatus> = _status.asStateFlow()

    fun dir(context: Context) = File(context.applicationContext.filesDir, DIR_NAME)

    fun isInstalled(context: Context) = File(dir(context), READY_MARKER).exists()

    fun refresh(context: Context) {
        if (_status.value is ModelStatus.Downloading) return
        _status.value = if (isInstalled(context)) ModelStatus.Installed else ModelStatus.NotInstalled
    }

    /** Runs in the background, so leaving the Settings screen doesn't cancel it. */
    fun download(context: Context) {
        if (job?.isActive == true) return
        val app = context.applicationContext
        job = scope.launch {
            _status.value = ModelStatus.Downloading(0)
            val target = dir(app)
            val zipFile = File(app.cacheDir, "vosk-model.zip")
            try {
                target.deleteRecursively()
                fetch(zipFile)
                unzip(zipFile, target)
                File(target, READY_MARKER).writeText("ok")
                _status.value = ModelStatus.Installed
            } catch (e: Exception) {
                target.deleteRecursively()
                _status.value = ModelStatus.Failed(e.message ?: "download failed")
            } finally {
                zipFile.delete()
            }
        }
    }

    private suspend fun fetch(destination: File) {
        val connection = URL(URL_ZIP).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 20_000
            connection.readTimeout = 30_000
            if (connection.responseCode !in 200..299) throw IOException("server said ${connection.responseCode}")
            val total = connection.contentLengthLong
            destination.parentFile?.mkdirs()
            connection.inputStream.use { input ->
                destination.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var copied = 0L
                    while (true) {
                        kotlinx.coroutines.currentCoroutineContext().ensureActive()
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                        copied += n
                        if (total > 0) _status.value = ModelStatus.Downloading((copied * 100 / total).toInt())
                    }
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun unzip(zipFile: File, target: File) {
        val root = target.canonicalFile
        ZipInputStream(zipFile.inputStream().buffered()).use { zip ->
            var entry = zip.nextEntry
            while (entry != null) {
                // Drop the archive's top folder ("vosk-model-small-en-in-0.4/...").
                val relative = entry.name.substringAfter('/', "")
                if (relative.isNotEmpty()) {
                    val out = File(root, relative).canonicalFile
                    if (!out.path.startsWith(root.path)) throw IOException("unsafe path in model archive")
                    if (entry.isDirectory) {
                        out.mkdirs()
                    } else {
                        out.parentFile?.mkdirs()
                        out.outputStream().use { zip.copyTo(it) }
                    }
                }
                entry = zip.nextEntry
            }
        }
    }
}

/** Listens for "Riley" in 16 kHz mono audio, fully offline. */
class WakeWordDetector(modelDir: File) : Closeable {
    private val model = Model(modelDir.absolutePath)

    // Restricting the vocabulary makes a small model a fast, cheap keyword spotter.
    private val recognizer = Recognizer(model, Mic.SAMPLE_RATE.toFloat(), GRAMMAR)

    fun accept(frame: ShortArray): Boolean {
        val isFinal = recognizer.acceptWaveForm(frame, frame.size)
        val json = runCatching { JSONObject(if (isFinal) recognizer.result else recognizer.partialResult) }.getOrNull()
        val heard = json?.optString(if (isFinal) "text" else "partial").orEmpty()
        if (WAKE_WORD in heard.split(' ')) {
            recognizer.reset()
            return true
        }
        return false
    }

    fun reset() = recognizer.reset()

    override fun close() {
        recognizer.close()
        model.close()
    }

    private companion object {
        const val WAKE_WORD = "riley"
        val GRAMMAR: String = JSONArray(listOf("riley", "hey riley", "okay riley", "[unk]")).toString()
    }
}
