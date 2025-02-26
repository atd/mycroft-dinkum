#!/usr/bin/env python3
import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from queue import Queue
from threading import Event, RLock, Semaphore, Thread
from typing import Any, Dict, Optional

from mycroft.messagebus import Message
from mycroft.messagebus.client import MessageBusClient
from mycroft.util.file_utils import resolve_resource_file
from mycroft.util.log import LOG

from .audio_hal import AudioHAL


class ForegroundChannel(IntEnum):
    """Available foreground channels (sound effects, TTS)"""

    EFFECT = 0
    SPEECH = 1


# Fixed sample rate for sound effects
EFFECT_SAMPLE_RATE = 48_000  # Hz
EFFECT_CHANNELS = 2


# -----------------------------------------------------------------------------


@dataclass
class TTSRequest:
    """Chunk of TTS audio to play.

    A single sentence or paragraph is typically split into multiple chunks for
    faster time to first audio.

    Chunks belonging to the same original sentence or paragraph share the same
    session id.
    """

    uri: str
    chunk_index: int
    num_chunks: int
    text: Optional[str] = None

    @property
    def is_first_chunk(self):
        return self.chunk_index <= 0

    @property
    def is_last_chunk(self):
        return self.chunk_index >= (self.num_chunks - 1)


@dataclass
class TTSSession:
    mycroft_session_id: Optional[str] = None
    chunk_queue: "Queue[TTSRequest]" = field(default_factory=Queue)
    speech_finished: Event = field(default_factory=Event)
    is_finished: bool = False


class RepeatingTimer(Thread):
    """Repeatedly calls a function at a fixed interval in a separate thread"""

    def __init__(self, interval: float, function):
        self.interval = interval
        self.function = function
        self.cancelled = False

        super().__init__()

    def cancel(self):
        self.cancelled = True

    def start(self):
        self.cancelled = False
        super().start()

    def run(self):
        seconds_to_wait = self.interval

        while True:
            if self.cancelled:
                break

            time.sleep(seconds_to_wait)

            if self.cancelled:
                break

            start_time = time.time()

            try:
                self.function()
            except Exception:
                LOG.exception("timer")

            end_time = time.time()

            if self.cancelled:
                break

            # Take run time of function into account to avoid drift
            seconds_elapsed = end_time - start_time
            seconds_to_wait = max(0, self.interval - seconds_elapsed)


# -----------------------------------------------------------------------------


class AudioUserInterface:
    """Audio interface between Mycroft and the Audio HAL.

    Listens for relevant bus events and manipulates the audio system.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

        self._ahal = AudioHAL(
            audio_sample_rate=EFFECT_SAMPLE_RATE, audio_channels=EFFECT_CHANNELS
        )

        # Sounds
        start_listening = self.config["sounds"]["start_listening"]
        self._start_listening_uri: Optional[str] = None

        if start_listening:
            self._start_listening_uri = "file://" + resolve_resource_file(
                start_listening
            )

        self._bg_position_timer = RepeatingTimer(1.0, self.send_stream_position)
        self._stream_session_id: Optional[str] = None

        self._sessions: Dict[str, TTSSession] = {}
        self._speech_thread: Optional[Thread] = None
        self._mycroft_session_id: Optional[str] = None
        self._speech_ready = Semaphore()
        # self._speech_finished = Event()
        self._session_lock = RLock()

        self._bus_events = {
            "recognizer_loop:record_begin": self.handle_start_listening,
            "recognizer_loop:record_end": self.handle_end_listening,
            "recognizer_loop:audio_output_start": self.handle_tts_started,
            "recognizer_loop:audio_output_end": self.handle_tts_finished,
            "mycroft.audio.play-sound": self.handle_play_sound,
            "mycroft.tts.stop": self._stop_tts,
            "mycroft.tts.session.start": self.handle_tts_session_start,
            # "mycroft.tts.session.end": self.handle_tts_session_end,
            "mycroft.tts.chunk.start": self.handle_tts_chunk,
            "mycroft.audio.hal.media.ended": self.handle_media_finished,
            # stream
            "mycroft.audio.service.play": self.handle_stream_play,
            "mycroft.audio.service.pause": self.handle_stream_pause,
            "mycroft.audio.service.resume": self.handle_stream_resume,
            "mycroft.audio.service.stop": self.handle_stream_stop,
        }

        self._is_running = True

    def initialize(self, bus: MessageBusClient):
        """Initializes the service"""
        self.bus = bus
        self._is_running = True
        self._ahal.initialize(self.bus)

        # TTS queue/thread
        self._speech_thread = Thread(target=self._speech_run, daemon=True)
        self._speech_thread.start()

        self._bg_position_timer.start()

        self._attach_events()

    def _attach_events(self):
        """Adds bus event handlers"""
        for event_name, handler in self._bus_events.items():
            self.bus.on(event_name, handler)

    def shutdown(self):
        """Shuts down the service"""
        try:
            self._is_running = False
            self._mycroft_session_id = None

            self._bg_position_timer.cancel()
            self._detach_events()

            if self._speech_thread is not None:
                self._ahal.stop_foreground(ForegroundChannel.SPEECH)
                self._speech_ready.release()
                self._speech_thread.join()
                self._speech_thread = None

            self._ahal.shutdown()
        except Exception:
            LOG.exception("error shutting down")

    def _detach_events(self):
        """Removes bus event handlers"""
        for event_name, handler in self._bus_events.items():
            self.bus.remove(event_name, handler)

    # -------------------------------------------------------------------------

    def _stop_tts(self, _message=None):
        with self._session_lock:
            self._mycroft_session_id = None

        # Stop any TTS currently speaking
        self._ahal.stop_foreground(ForegroundChannel.SPEECH)

    def _duck_volume(self):
        """Lower background stream volumes during voice commands"""
        self._ahal.set_background_volume(
            1.0 - self.config["listener"]["duck_while_listening"]
        )
        LOG.info("Ducked volume")

    def _unduck_volume(self):
        """Restore volumes after voice commands"""
        self._ahal.set_background_volume(1.0)
        LOG.info("Unducked volume")

    # -------------------------------------------------------------------------

    def handle_play_sound(self, message):
        """Handler for skills' play_sound_uri"""
        uri = message.data.get("uri")
        volume = message.data.get("volume")
        mycroft_session_id = message.data.get("mycroft_session_id")
        self._play_effect(uri, volume=volume, mycroft_session_id=mycroft_session_id)

    def handle_start_listening(self, _message):
        """Play sound when Mycroft begins recording a command"""

        self._duck_volume()
        self._stop_tts()

        if self._start_listening_uri and (self.config.get("confirm_listening", False)):
            self._play_effect(self._start_listening_uri)

    def handle_end_listening(self, _message):
        self._unduck_volume()

    def _play_effect(
        self,
        uri: str,
        volume: Optional[float] = None,
        mycroft_session_id: Optional[str] = None,
    ):
        """Play sound effect from uri"""
        if uri:
            assert uri.startswith("file://"), "Only file URIs are supported for effects"
            file_path = uri[len("file://") :]
            self._ahal.play_foreground(
                ForegroundChannel.EFFECT,
                file_path,
                volume=volume,
                mycroft_session_id=mycroft_session_id,
            )
            LOG.info("Played sound: %s", uri)

    def handle_tts_session_start(self, message):
        mycroft_session_id = message.data.get("mycroft_session_id")

        with self._session_lock:
            is_different_session = mycroft_session_id != self._mycroft_session_id

            session = self._sessions.get(mycroft_session_id)
            if session is None:
                session = TTSSession(mycroft_session_id=mycroft_session_id)
                self._sessions[mycroft_session_id] = session

            session.is_finished = False
            self._mycroft_session_id = mycroft_session_id

            if is_different_session:
                # Stop any TTS currently speaking
                self._ahal.stop_foreground(ForegroundChannel.SPEECH)

        LOG.debug(
            "Started TTS session %s",
            mycroft_session_id,
        )

    def handle_tts_chunk(self, message):
        """Queues a text to speech audio chunk to be played"""
        uri = message.data["uri"]
        mycroft_session_id = message.data.get("mycroft_session_id", "")
        chunk_index = message.data.get("chunk_index", 0)
        num_chunks = message.data.get("num_chunks", 1)
        text = message.data.get("text")

        with self._session_lock:
            if mycroft_session_id != self._mycroft_session_id:
                # Doesn't match session from tts.session.start
                LOG.debug(
                    "Dropping TTS chunk from cancelled session %s: %s",
                    mycroft_session_id,
                    text,
                )
                return

        session = self._sessions.get(mycroft_session_id)
        if session is not None:
            request = TTSRequest(
                uri=uri,
                chunk_index=chunk_index,
                num_chunks=num_chunks,
                text=text,
            )
            session.chunk_queue.put(request)
            self._speech_ready.release()

            LOG.info(
                "Queued TTS chunk %s/%s: %s (session=%s): %s",
                chunk_index + 1,
                num_chunks,
                uri,
                mycroft_session_id,
                text,
            )
        else:
            LOG.error("TTS session was not started: %s", mycroft_session_id)

    def handle_tts_started(self, _message: Message):
        self._duck_volume()

    def handle_tts_finished(self, _message: Message):
        self._unduck_volume()

    def handle_media_finished(self, message):
        """Callback when VLC media item has finished playing"""
        channel = message.data.get("channel")
        background = message.data.get("background", False)
        media_id = message.data.get("media_id")

        if channel == ForegroundChannel.SPEECH:
            # Signal speech thread to play next TTS chunk
            session = self._sessions.get(media_id)
            if session is not None:
                LOG.debug("TTS chunk finished playing for session %s", media_id)
                session.speech_finished.set()
        elif background:
            # Signal background stream complete
            LOG.info("Background stream finished")
            self.bus.emit(
                Message(
                    "mycroft.audio.queue_end",
                    data={"mycroft_session_id": self._stream_session_id},
                )
            )

    def _speech_run(self):
        """Thread proc for text to speech"""
        try:
            while self._is_running:
                self._speech_ready.acquire()

                with self._session_lock:
                    session = self._sessions.get(self._mycroft_session_id)
                    if session is None:
                        # No session for id
                        continue

                    # Clean up other sessions
                    all_session_ids = list(self._sessions.keys())
                    for session_id in all_session_ids:
                        if session_id != self._mycroft_session_id:
                            cancelled_session = self._sessions.pop(session_id, None)
                            # Finish session to ensure events are sent out
                            if not cancelled_session.is_finished:
                                cancelled_session.is_finished = True
                                self._finish_tts_session(
                                    mycroft_session_id=session_id,
                                )

                            LOG.debug("Cleaned up TTS session %s", session_id)

                if session.chunk_queue.empty():
                    # No TTS chunks
                    continue

                request = session.chunk_queue.get()
                self.bus.emit(
                    Message(
                        "recognizer_loop:audio_output_start",
                        data={"mycroft_session_id": session.mycroft_session_id},
                    )
                )

                # TODO: Support other URI types
                assert request.uri.startswith("file://")
                file_path = request.uri[len("file://") :]

                self.bus.emit(
                    Message(
                        "mycroft.tts.chunk.started",
                        data={
                            "mycroft_session_id": session.mycroft_session_id,
                            "chunk_index": request.chunk_index,
                            "num_chunks": request.num_chunks,
                            "uri": request.uri,
                            "text": request.text,
                        },
                    )
                )

                # Play TTS chunk
                session.speech_finished.clear()
                if os.path.isfile(file_path):
                    duration_sec = self._ahal.play_foreground(
                        ForegroundChannel.SPEECH,
                        file_path,
                        media_id=session.mycroft_session_id,
                        mycroft_session_id=session.mycroft_session_id,
                    )

                    if duration_sec is not None:
                        LOG.info(
                            "Speaking TTS chunk %s/%s for %s sec from session %s",
                            request.chunk_index + 1,
                            request.num_chunks,
                            duration_sec,
                            session.mycroft_session_id,
                        )

                        # Wait at most a half second after TTS should have been finished.
                        # This event is set whenever TTS is cleared.
                        timeout = duration_sec + 0.5
                        session.speech_finished.wait(timeout=timeout)

                self.bus.emit(
                    Message(
                        "mycroft.tts.chunk.ended",
                        data={
                            "mycroft_session_id": session.mycroft_session_id,
                            "chunk_index": request.chunk_index,
                            "num_chunks": request.num_chunks,
                            "uri": request.uri,
                            "text": request.text,
                        },
                    )
                )

                if request.is_last_chunk and (not session.is_finished):
                    session.is_finished = True
                    self._finish_tts_session(
                        mycroft_session_id=session.mycroft_session_id,
                    )

        except Exception:
            LOG.exception("error is speech thread")

    def _finish_tts_session(
        self,
        mycroft_session_id: str,
    ):
        # Report speaking finished for speak(wait=True)
        self.bus.emit(
            Message(
                "mycroft.tts.session.ended",
                data={
                    "mycroft_session_id": mycroft_session_id,
                },
            )
        )

        self.bus.emit(
            Message(
                "recognizer_loop:audio_output_end",
                data={"mycroft_session_id": mycroft_session_id},
            )
        )

        LOG.info("TTS session finished: %s", mycroft_session_id)

    # -------------------------------------------------------------------------

    def handle_stream_play(self, message: Message):
        """Handler for mycroft.audio.service.play

        Play tracks using the background stream.
        """
        tracks = message.data.get("tracks", [])
        if not tracks:
            LOG.warning("Play message received with not tracks: %s", message.data)
            return

        uri_playlist = []
        for track in tracks:
            if isinstance(track, str):
                # URI
                uri_playlist.append(track)
            else:
                # (URI, mimetype)
                uri = next(iter(track))
                uri_playlist.append(uri)

        # Stop previous stream
        self._ahal.stop_background()

        self._stream_session_id = message.data.get("mycroft_session_id")
        LOG.info(
            "Playing background stream: %s (session=%s)",
            uri_playlist,
            self._stream_session_id,
        )
        self._ahal.start_background(uri_playlist)
        self.bus.emit(
            Message(
                "mycroft.audio.service.playing",
                data={"mycroft_session_id": self._stream_session_id},
            )
        )

    def handle_stream_pause(self, message: Message):
        """Handler for mycroft.audio.service.pause"""
        mycroft_session_id = message.data.get("mycroft_session_id")
        if mycroft_session_id == self._stream_session_id:
            LOG.debug("Pausing background stream (session=%s)", mycroft_session_id)
            self._ahal.pause_background()
            self.bus.emit(
                Message(
                    "mycroft.audio.service.paused",
                    data={"mycroft_session_id": mycroft_session_id},
                )
            )

    def handle_stream_resume(self, message: Message):
        """Handler for mycroft.audio.service.resume"""
        mycroft_session_id = message.data.get("mycroft_session_id")
        if mycroft_session_id == self._stream_session_id:
            LOG.debug("Resuming background stream (session=%s)", mycroft_session_id)
            self._ahal.resume_background()
            self.bus.emit(
                Message(
                    "mycroft.audio.service.resumed",
                    data={"mycroft_session_id": mycroft_session_id},
                )
            )

    def handle_stream_stop(self, message):
        """Handler for mycroft.audio.service.stop"""
        mycroft_session_id = message.data.get("mycroft_session_id")
        if mycroft_session_id == self._stream_session_id:
            LOG.debug("Stopping background stream (session=%s)", mycroft_session_id)

            # Don't ever actually stop the background stream.
            # This lets us resume it later at any point.
            self._ahal.pause_background()
            self.bus.emit(
                Message(
                    "mycroft.audio.service.stopped",
                    data={"mycroft_session_id": mycroft_session_id},
                )
            )

    def send_stream_position(self):
        """Sends out background stream position to skills"""
        if not self._ahal.is_background_playing():
            return

        position_ms = self._ahal.get_background_time()
        if position_ms >= 0:
            self.bus.emit(
                Message(
                    "mycroft.audio.service.position",
                    data={
                        "mycroft_session_id": self._stream_session_id,
                        "position_ms": position_ms,
                    },
                )
            )
