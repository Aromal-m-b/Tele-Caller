# Natural female speech output

The main microphone mode now speaks public support responses through Deepgram
Aura-2 Andromeda (`aura-2-andromeda-en`) while keeping the full answer on screen. Deepgram
describes Andromeda as casual, expressive and comfortable.
This is a stock AI voice, not a recording of a human agent.

After the STT socket and microphone connect, Maya speaks first. Standard Gemini
generation creates one short opening per session with varied phrasing, introducing
Maya from Sun Direct and asking how to help. No retrieval, customer data or account
tool call is used for this opening. It is retained as an assistant message in later
prompts. If generation fails or returns invalid text, a short local fallback keeps
the call usable. Identity questions with conversational filler get direct answers;
later greetings do not repeat the full introduction.

Conversational replies now come from Gemini 3.8 Flash (`gemini-3.8-flash`, low thinking),
using the opening, recent text dialogue and compressed older text context. This
replaces the local name/greeting/courtesy answer tables. The user explicitly
authorized sending conversational text and recent dialogue to Gemini. Hard abuse
checks still run first. Conversation generation has no retrieval or account tools.
Support requests continue through the existing grounded RAG/tool path.

Repeat requests replay the actual previous response, including an interrupted
response or the opening, rather than inventing a paraphrase. Gemini can also infer
repeat intent from less explicit wording. Private results remain local; their
existing safe speech prompts are retained when repeated. No private result values
are added to Gemini's text history. Six recent exchanges plus the compressed older
session archive retain text context. Private call transcripts and usage estimates
are saved locally under `output/calls`; the older-session archive stays in RAM.

```powershell
.\.venv-voice\Scripts\python.exe main.py
.\.venv-voice\Scripts\python.exe main.py --demo-customer CUST00001
# Microphone input without speaker output:
.\.venv-voice\Scripts\python.exe main.py --silent
```

`DEEPGRAM_TTS_MODEL` selects the voice in `.env`; `--tts-voice` overrides it for a
run. `--output-device` selects a PyAudio speaker device. `--text` and `--question`
remain silent keyboard/debugging modes. The existing `DEEPGRAM` key is used, with
`DEEPGRAM_API_KEY` accepted as a fallback. No Gemini audio or Live API is involved.

## Sound and playback

The text formatter removes Markdown headings/list markers, uses sentence pauses,
speaks rupee/paise amounts naturally, and changes ISO dates into readable dates.
Facts, caveats and confirmation wording are retained. Native Aura-2 voice prosody
is used; no arbitrary filler phrases or artificial robotic pitch manipulation is
added. Long responses are split at sentence/word boundaries to respect request limits.

Raw 24 kHz mono 16-bit PCM normally streams over a reusable Aura socket, with HTTP
fallback. `DEEPGRAM_TTS_TRANSPORT=http` forces HTTP. See [streaming details](streaming.md).
The
speaker drains before another turn is considered complete. Failed speech leaves
the complete text response available and reports that playback is unavailable.

During playback, local WebRTC voice detection watches microphone audio. Sustained
activity (120 ms) opens a 1.5-second transcription window while playback continues.
Only recognized words with at least 0.75 confidence can cancel the active TTS request
and remaining playback. Empty/noise events and transcripts matching the assistant's
current response do not interrupt it. Single-word interruptions are restricted to
short conversational controls such as "stop", "wait" and "hello". A short
microphone pre-roll is sent to STT so the first words of the interruption are retained.
Output writes are 20 ms rather than large buffered blocks. A direct playback-reference
filter reduces speaker echo, but does not replace room acoustic echo cancellation.
Use headphones for reliable interruptions; loud speakers or reverberation can cause
false candidates; noisy or incorrectly recognized speech can still cause false
interruptions. No microphone recording is saved. Ctrl+C ends the session.

Audio checks and short social turns are interpreted by Gemini, without retrieval
or tool selection. Explicit programming/abuse requests still trigger the guard.

## Private account responses

Full MongoDB account values currently remain on screen. Private tools provide
fixed, non-sensitive speech prompts such as "Your package and validity details are
shown on the screen." The TTS request does not include customer names, contact
fields, subscription dates, payment amounts or transaction identifiers from these
results. Public catalog information can be spoken, including its illustrative-price
notice. Account errors use the application-defined error message.

Automatic approval review rejected sending full personal/account/payment responses
to Deepgram without explicit authorization for that additional external processing.
The privacy-aware speech path is implemented now. Reading those private values
aloud through Deepgram remains pending explicit user authorization.

## Verification and preview

```powershell
.\.venv-voice\Scripts\python.exe -m pytest
.\.venv-voice\Scripts\python.exe -m tests.qualify_speech_output
```

The live output check generates `output/audio/maya-andromeda-preview.wav` from a public
audio-check response. The default speaker previously passed
an open/write-silence/drain/close check. Tests verify formatting, PCM alignment,
speech completion, echo prevention, safe spoken account prompts, resource cleanup,
failure fallback, and default spoken versus `--silent` output. Voice naturalness is
subjective; the generated preview allows listening before choosing another voice.
