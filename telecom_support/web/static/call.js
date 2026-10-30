/* Browser audio transport only. Call reasoning and all service tools stay server-side. */
(() => {
  const $ = id => document.getElementById(id);
  let room = null, session = null, poll = null, closing = false, muted = false, access = '';
  const labels = {connecting:'Connecting to LiveKit…',waiting_for_microphone:'Waiting for microphone…',starting_agent:'Starting Maya…',connected:'Conversation connected',ended:'Call ended',error:'Connection ended'};
  function show(state, message) { $('connection').textContent = labels[state] || state; $('message').textContent = message; }
  async function request(path, options = {}) {
    const response = await fetch(path, {...options, headers:{'X-Call-Access':access}, cache:'no-store'});
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The session request failed.');
    return body;
  }
  async function end(notify = true) {
    if (closing) return;
    closing = true;
    clearInterval(poll); poll = null;
    const previous = session, previousRoom = room;
    session = null; room = null;
    if (previousRoom) await previousRoom.disconnect();
    $('remote-audio').replaceChildren(); $('orb').classList.remove('active');
    $('start').disabled = false; $('end').disabled = true; $('mute').disabled = true;
    $('code').disabled = false; $('sound').hidden = true; muted = false; $('mute').textContent = 'Mute microphone';
    if (notify && previous) {
      try { await request('/api/sessions/' + encodeURIComponent(previous.session_id), {method:'DELETE'}); }
      catch (_) { /* Leaving the RTC room also ends the server session. */ }
    }
    closing = false;
  }
  $('start').addEventListener('click', async () => {
    access = $('code').value.trim();
    if (!access) { show('Access code needed','Enter WEBRTC_ACCESS_CODE from your local .env file.'); return; }
    if (!window.isSecureContext || !navigator.mediaDevices) { show('Secure connection needed','Open this page on localhost or through the HTTPS ngrok URL.'); return; }
    if (!window.LivekitClient) { show('Client unavailable','The LiveKit browser library could not load. Check the server installation.'); return; }
    $('start').disabled = true; $('code').disabled = true;
    show('connecting','Connecting your microphone to the voice session…');
    try {
      const {Room,RoomEvent,Track} = LivekitClient;
      const currentRoom = new Room({adaptiveStream:true,dynacast:true});
      room = currentRoom;
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) { const element = track.attach(); element.autoplay = true; $('remote-audio').append(element); }
      });
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => { $('sound').hidden = !room || room.canPlaybackAudio; });
      room.on(RoomEvent.Disconnected, async () => { if (room === currentRoom && !closing && session) { await end(); show('ended','The voice connection has ended. You can start another conversation.'); } });
      await room.startAudio();
      session = await request('/api/sessions', {method:'POST'});
      await room.connect(session.url,session.token);
      await room.localParticipant.setMicrophoneEnabled(true,{echoCancellation:true,noiseSuppression:true,autoGainControl:true});
      $('end').disabled = false; $('mute').disabled = false; $('orb').classList.add('active');
      show('starting_agent','Maya will introduce herself when the agent is ready.');
      poll = setInterval(async () => {
        if (!session) return;
        const polledSession = session;
        try {
          const state = await request('/api/sessions/' + encodeURIComponent(polledSession.session_id));
          if (session !== polledSession) return;
          if (state.status === 'ended' || state.status === 'error') {
            await end(false);
            show(state.status,state.status === 'error' ? 'The agent could not continue. Check the private server log, then try again.' : 'Thanks for calling. You can start another conversation.');
          } else show(state.status,state.status === 'connected' ? 'Speak naturally. You can interrupt Maya at any time.' : 'Preparing your voice session…');
        } catch (_) { /* Transient poll failures do not disconnect working audio. */ }
      },1500);
    } catch (error) { await end(); show('Connection problem',error.message || 'Check microphone permission and the server log.'); }
  });
  $('end').addEventListener('click', async () => { await end(); show('ended','Your session is closed. Start another whenever you’re ready.'); });
  $('mute').addEventListener('click', async () => {
    if (!room) return;
    try { muted = !muted; await room.localParticipant.setMicrophoneEnabled(!muted); $('mute').textContent = muted ? 'Unmute microphone' : 'Mute microphone'; }
    catch (_) { show('Microphone unavailable','Check microphone permission in your browser.'); }
  });
  $('sound').addEventListener('click', async () => { if (room) await room.startAudio(); });
  window.addEventListener('pagehide', () => {
    if (session) fetch('/api/sessions/' + encodeURIComponent(session.session_id), {method:'DELETE',headers:{'X-Call-Access':access},keepalive:true}).catch(()=>{});
  });
})();
