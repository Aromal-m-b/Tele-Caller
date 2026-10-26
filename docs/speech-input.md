# Microphone input for the main assistant

The supplied `caller_stream.py` reference was inspected and adapted into
`telecom_support/speech_input.py`. The reference file was not edited.
The implementation uses its Deepgram listen/v1 WebSocket approach, Nova-3 model,
16 kHz mono signed 16-bit PCM and configurable 1500 ms endpointing. It uses the existing
`DEEPGRAM` key, accepting `DEEPGRAM_API_KEY` as a fallback for compatibility.

```powershell
.\.venv-voice\Scripts\python.exe main.py
# With the synthetic account fixture:
.\.venv-voice\Scripts\python.exe main.py --demo-customer CUST00001
# Keyboard debugging:
.\.venv-voice\Scripts\python.exe main.py --text
```

Speak a question naturally; complete transcribed turns appear after `You:` and
the existing assistant's text reply appears after `A:`. Say `stop listening`,
`end call`, `quit` or `exit` to finish, or press Ctrl+C. Public replies are now also
spoken with Deepgram Andromeda. Private account values remain on screen, accompanied
by a safe spoken prompt. See `speech-output.md` for output behavior.

Options:

- `--input-device INDEX`: select a PyAudio input device; otherwise system default.
- `--stt-language CODE`: override `DEEPGRAM_STT_LANGUAGE`, default `en-US`.
- `--text`: the previous typed menu.
- `--silent`: microphone input with text-only replies.
- `--tts-voice MODEL` and `--output-device INDEX`: select the speech voice/speaker.
- `--question TEXT`: one typed request, without opening a microphone.
- All existing account, source and index options remain available.

Audio dependencies are included in `requirements.txt`; no Deepgram SDK is needed.
Microphone permission and a 16 kHz mono input device are required. If capture fails,
the application reports a clear startup error and releases any opened resources.

## Ending a call

Direct requests such as "hang up", "please disconnect this call" and "stop
listening" end the microphone session. Maya says goodbye and closes microphone,
speaker and Deepgram connection. Negated requests such as "don't hang up" do not
authorize a disconnect. A direct request also stops pending reply playback.

If the caller indicates they are finished, Maya asks "Would you like me to end the
call?" Gemini can suggest this action for natural paraphrases. The session ends
only on a subsequent affirmative answer. A no keeps it open; a new question cancels
the pending hang-up prompt. An unrelated yes, including recharge confirmation, does
not authorize a hang-up. End-call confirmation is separate from account operations.

After 15 seconds without recognized caller words, Maya checks: "Hello again. Can
you hear me?" At 45 seconds of caller inactivity she gives the final still-there
warning. After another 20 seconds without a response, she says goodbye and disconnects.
The wording above describes intent, not a fixed script: Gemini generates the early
check-in, warning, goodbye, hang-up confirmation and acknowledgement from safe recent
text context. Generation failure alone uses a short fallback. Timers and consent
decisions remain application-controlled. If caller activity arrives during idle
message generation, that stale message is discarded before speech/disconnection.
Audibility confirmations stay separate from account and hang-up confirmations.
Recognized caller activity resets the idle deadline; raw microphone noise does not.
Backend processing and assistant playback do not consume the caller's idle budget.
Configure `.env` with `CALL_IDLE_TIMEOUT_SECONDS` and `CALL_IDLE_GRACE_SECONDS`, plus `CALL_IDLE_CHECKIN_SECONDS` for the first hint, or
use `--idle-timeout SECONDS --idle-grace SECONDS`. A timeout of zero disables idle
disconnect. Grace must be positive. This closes the current local audio session;
there is no PSTN/telephony provider integrated into this CLI.

## Transcripts and processing

Microphone PCM goes to Deepgram. Only finalized utterance text goes to the existing
text assistant. Interim transcripts are not treated as customer requests. Stable
segments accumulate until `speech_final` or an `UtteranceEnd` boundary, avoiding
responses to partial phrases and duplicate endpoint events.

Microphone and socket traffic continue while a serialized worker handles reasoning,
FAISS, MongoDB and memory. Only one customer request mutates session state at a time.
Bounded audio/turn queues report overflow instead of silently dropping customer data.
On cancellation, device/socket capture stops and an already running synchronous
backend request finishes before the database client closes.
During playback, local speech detection opens a transcription window. Confident
recognized words, rather than microphone activity alone, can cancel the response.
Microphone pre-roll preserves the first words. Headphones are recommended:
the direct echo filter is not a full room acoustic echo canceller.

Spoken confirmations accept ASR punctuation such as "Yes, please.". A yes spoken
before the previous package details were displayed is not applied to that selection.
Authentication and read-only account restrictions remain in place; a spoken customer
ID is not authentication. Database result values still stay local to the application.
Audio files and microphone recordings are not written by normal application use.

## Verification

Tests cover final/interim segmentation, complete utterance delivery, ordered backend
processing, nonblocking audio, punctuation, early confirmations, stop/cancel cleanup,
device overflow, key aliases, and audio-default versus explicit text CLI behavior.
The default Windows input reported 16 kHz mono support, and a local capture probe
returned a 2,048-byte frame that was discarded rather than uploaded.

A generated synthetic WAV was streamed through the real Deepgram endpoint and into
the current assistant successfully; see `reports/speech-input-integration.json`.
The synthetic audio test did not upload microphone audio. Subjective recognition
quality for the user's actual voice, room and device remains a user acceptance check.
The endpoint behavior follows [Deepgram's documented final/endpoint handling](https://developers.deepgram.com/docs/understand-endpointing-interim-results).
