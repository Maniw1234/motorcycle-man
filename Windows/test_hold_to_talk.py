import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import motorcycle_man as app

# Hold to talk on the phone page: one finger, one talk key, one send (or a Send it offer, or nothing).
# Node, where it is installed, runs the page's own talk code in a pretend page with a pretend clock: no browser,
# microphone, PC or internet.
ANDROID = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Android")
NODE = shutil.which("node")


def talk_code(page: str) -> str:
    """The page's talk code: the reply guard, the talk state with the app's callbacks, and the press itself."""
    parts = (("function speakReply(text) {", "\nfunction submitTyped()", False),
             ("// Talking: hold the face", "\n// Several PCs:", False),
             ("function androidSpeechEnded() {", "window.addEventListener('pagehide', () => dropTalk(LEFT_WORDS));", True))
    code = []
    for start, end, keep_end in parts:
        at = page.index(start)
        stop = page.index(end, at)
        code.append(page[at:stop + (len(end) if keep_end else 0)])
    return "\n".join(code)


def run_node(script: str, code: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path, code_path = os.path.join(temp_dir, "check.js"), os.path.join(temp_dir, "talk.js")
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write(script)
        with open(code_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        return subprocess.run([NODE, script_path, code_path], capture_output=True, text=True, timeout=60)


# The talk rules block, run on its own.
RULES_JS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const block = src.split('// ---- talk-rules:start')[1].split('// ---- talk-rules:end')[0];
const rules = new Function(block.replace(/^[^\n]*\n/, '') + '; return {pressKind, joinWords, fastEmpty, restartDelay, listenOptionsFrom};')();
const eq = (a, b) => { if (a !== b) throw new Error(JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
eq(rules.pressKind(120, false, false), 'tap');
eq(rules.pressKind(600, false, false), 'tap');    // a slow tap that heard nothing yet
eq(rules.pressKind(600, true, false), 'hold');
eq(rules.pressKind(1300, false, false), 'hold');  // past the slow tap: "I didn't hear anything"
eq(rules.pressKind(5000, false, false), 'hold');
eq(rules.pressKind(5000, true, true), 'tap');     // the widget and tile
eq(rules.joinWords('press tab', 'tab tab'), 'press tab tab tab');
eq(rules.joinWords('open notepad', ' and  type hi '), 'open notepad and type hi');
eq(rules.joinWords('', 'hi'), 'hi');
eq(rules.joinWords('hi', ''), 'hi');
eq(rules.joinWords('', ''), '');
eq(rules.fastEmpty(400, false), true);
eq(rules.fastEmpty(400, true), false);
eq(rules.fastEmpty(2500, false), false);
eq([0, 1, 2, 3, 9].map(rules.restartDelay).join(','), '0,150,600,900,900');
// The names for the app: trimmed, no repeats, none longer than 40 characters, at most 50; ownMic only when true.
eq(rules.listenOptionsFrom([' Motorcycle  Man ', 'Spotify', 'spotify', '', 7, 'x'.repeat(41)], false),
   '{"hints":["Motorcycle Man","Spotify"]}');
eq(JSON.parse(rules.listenOptionsFrom(Array.from({length: 80}, (_, i) => 'app ' + i))).hints.length, 50);
eq(rules.listenOptionsFrom(undefined, true), '{"hints":[],"ownMic":true}');
eq(rules.listenOptionsFrom('Spotify', 'yes'), '{"hints":[]}');
console.log('rules ok');
"""

# Presses played through the page's talk code: each way of listening, taps, holds, pauses, a second finger,
# Android taking the touch, leaving the app, late answers. It stops at the first wrong step.
FLOWS_JS = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');

function page(opts) {
  let now = 1000, timerId = 0, timers = [];
  const calls = [], fetches = [];
  const listeners = {};
  function element(id) {
    const classes = new Set();
    return {
      id, textContent: '', style: {display: '', props: {}, setProperty(k, v) { this.props[k] = v; }, removeProperty(k) { delete this.props[k]; }},
      classList: {add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)),
                  toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: c => classes.has(c)},
      captured: null, setPointerCapture(id) { this.captured = id; },
      addEventListener(type, fn) { (listeners[id + ':' + type] = listeners[id + ':' + type] || []).push(fn); },
    };
  }
  const els = {};
  const document = {
    visibilityState: 'visible',
    getElementById: id => (els[id] = els[id] || element(id)),
    addEventListener(type, fn) { (listeners['document:' + type] = listeners['document:' + type] || []).push(fn); },
  };
  class FakeSR {
    constructor() { this.running = false; FakeSR.last = this; }
    start() { if (this.running) throw new Error('InvalidStateError'); this.running = true; calls.push('sr.start'); }
    stop() { calls.push('sr.stop'); }
    abort() { calls.push('sr.abort'); }
  }
  const bridge = {
    isAvailable: () => true,
    startListening: lang => calls.push('app.startListening ' + lang),
    stopListening: () => calls.push('app.stopListening'),
    speak: () => calls.push('app.speak'), stopSpeaking: () => calls.push('app.stopSpeaking'),
  };
  if (opts.engine === 'app') {
    Object.assign(bridge, {
      listen: (lang, talk, o) => calls.push('app.listen ' + lang + ' ' + talk + ' ' + o),
      listenUntilQuiet: talk => calls.push('app.listenUntilQuiet ' + talk),
      doneListening: talk => calls.push('app.doneListening ' + talk),
      cancelListening: talk => calls.push('app.cancelListening ' + talk),
      fingerDown: () => { calls.push('app.fingerDown'); return !!opts.fingerStillDown; },
    });
  }
  const window = {
    MotorcycleManAndroid: opts.engine === 'app' || opts.engine === 'app1' ? bridge : undefined,
    SpeechRecognition: opts.engine === 'browser' ? FakeSR : undefined,
    speechSynthesis: {cancel() {}, speak() { calls.push('tts.speak'); }},
    crypto: {getRandomValues: a => { a.set([0xab, 0xcd, 0x01, 0x23]); return a; }},
    addEventListener(type, fn) { (listeners['window:' + type] = listeners['window:' + type] || []).push(fn); },
  };
  const answers = opts.answers || {};
  const context = {
    window, document, console,
    navigator: {language: opts.lang || 'en-US', userAgent: 'Chrome/130', vibrate: () => calls.push('vibrate')},
    location: {protocol: opts.engine === 'pc' ? 'http:' : 'https:'},
    crypto: {getRandomValues: a => { a.set([0xab, 0xcd, 0x01, 0x23]); return a; }},
    Uint8Array, Array, String, Number, Math, JSON, Object, Promise, Error, URLSearchParams,
    Date: {now: () => now},
    setTimeout: (fn, ms) => { const id = ++timerId; timers.push({id, at: now + (ms || 0), fn}); return id; },
    clearTimeout: id => { timers = timers.filter(t => t.id !== id); },
    fetch: url => {
      fetches.push(url);
      const route = url.split('?')[0];
      const answer = answers[route] !== undefined ? answers[route] : '{"reply":"Done!"}';
      return Promise.resolve({ok: true, text: () => Promise.resolve(answer), json: () => Promise.resolve(JSON.parse(answer))});
    },
    SpeechSynthesisUtterance: function () {},
  };
  window.speechSynthesis && (context.speechSynthesis = window.speechSynthesis);
  vm.createContext(context);
  vm.runInContext(`
    const phoneToken = 'tok';
    function requestUrl(path, params) { const q = new URLSearchParams(params || {}); q.set('token', phoneToken); return path + '?' + q.toString(); }
    let nx = 0.5, holding = false, busy = false, lastSay = '', mode = 'idle', awaitingReply = false, commandSeq = 0;
    let talkBack = true, replyAloud = false, stopRequested = false;
    const faceEl = document.getElementById('face'), stopBtn = document.getElementById('stopBtn');
    const said = [], chips = [], modes = [];
    function say(t) { said.push(t); }
    function setChip(s, t) { chips.push(t); }
    function setMode(m) { mode = m; modes.push(m); }
    function wake() {} function showHint() {} function react() {} function replyMood() { return 'happy'; }
    function spokenWords(t) { return t; } function showTalkBack() {} function updateStop() {} function closeMenu() {}
    function openScreen() {} function openRemote() {}
    let pausedTask = null; const barHidden = []; function hidePaused(quiet) { barHidden.push(quiet); pausedTask = null; }
  `, context);
  vm.runInContext(code, context);
  const g = expr => vm.runInContext(expr, context);
  const P = {
    g, calls, fetches, els, context,
    said: () => g('said'), chips: () => g('chips'),
    lastSaid: () => { const s = g('said'); return s[s.length - 1]; },
    async settle() { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); },
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at || a.id - b.id);
        const next = timers[0];
        if (!next || next.at > until) break;
        timers.shift();
        now = next.at;
        next.fn();
        await P.settle();
      }
      now = until;
      await P.settle();
    },
    fire(key, event) { (listeners[key] || []).forEach(fn => fn(event)); },
    event(type, el, pointerId, extra) {
      return Object.assign({type, pointerId, isPrimary: true, button: 0, currentTarget: el, target: el,
                            defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}, extra || {});
    },
    async down(id = 1, extra) {
      const ev = P.event('pointerdown', P.els[(extra && extra.on) || 'stage'], id, extra);
      P.fire(((extra && extra.on) || 'stage') + ':pointerdown', ev);
      await P.settle();
      return ev;
    },
    async up(id = 1) { P.fire('window:pointerup', P.event('pointerup', P.els.stage, id)); await P.settle(); },
    sends: () => fetches.filter(u => u.startsWith('/hold_stop')),
    param: (url, k) => new URLSearchParams(url.split('?')[1]).get(k),
    sr: () => FakeSR.last,
    async srEnd() { const r = FakeSR.last; r.running = false; r.onend(); await P.settle(); },
    key: () => g('talkKey'),
    async hide() { document.visibilityState = 'hidden'; P.fire('document:visibilitychange', {}); await P.settle(); },
  };
  return P;
}
const eq = (a, b, what) => { if (a !== b) throw new Error((what || '') + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const ok = (v, what) => { if (!v) throw new Error('not true: ' + what); };
const READY = '{"ok":true,"ready":true,"error":""}';

(async () => {
  // 1. PC microphone, a hold: one start, "Listening" only after the PC says it started, one stop when let go.
  {
    const P = page({engine: 'pc', answers: {'/hold_start': READY}});
    const ev = await P.down();
    ok(ev.defaultPrevented, 'pointerdown default prevented');
    eq(P.els.stage.captured, 1, 'captured');
    eq(P.fetches.filter(u => u.startsWith('/hold_start')).length, 1, 'one hold_start');
    ok(/s=abcd0123-1/.test(P.fetches[0]), 'talk key on hold_start');
    eq(P.chips()[0], 'Getting ready', 'chip before ready');
    eq(P.chips()[1], 'Listening', 'chip after the PC started');
    await P.advance(2000);
    await P.up();
    eq(P.sends().length, 1, 'one hold_stop');
    eq(P.param(P.sends()[0], 'auto'), null, 'a hold is not auto');
    eq(P.param(P.sends()[0], 's'), 'abcd0123-1', 'stop key');
    eq(P.g('talkPhase'), 'off', 'pc press over');
    await P.up();
    eq(P.sends().length, 1, 'a second up sends nothing');
  }
  // 2. PC microphone, a tap: auto stop at once; a second tap is "done talking"; the answer ends the listening.
  {
    const P = page({engine: 'pc', answers: {'/hold_start': READY}});
    await P.down();
    await P.advance(100);
    await P.up();
    eq(P.sends().length, 1, 'tap sends its auto stop');
    eq(P.param(P.sends()[0], 'auto'), '1', 'auto');
    eq(P.g('tapListening'), false, 'answer came (Done!) so listening is over');
    // Again, with the answer held back: another tap sends /listen_stop.
    const Q = page({engine: 'pc', answers: {'/hold_start': READY}});
    Q.context.fetch = (orig => url => { if (url.startsWith('/hold_stop')) { Q.fetches.push(url); return new Promise(() => {}); } return orig(url); })(Q.context.fetch);
    await Q.down();
    await Q.advance(100);
    await Q.up();
    eq(Q.g('tapListening'), 'pc', 'pc listens until quiet');
    eq(Q.g('talkPhase'), 'off', 'tap press already sent');
    await Q.down(2);
    ok(Q.fetches.some(u => u.startsWith('/listen_stop') && /s=abcd0123-1/.test(u)), 'listen_stop with the key');
    eq(Q.sends().length, 1, 'still one send');
  }
  // 3. Browser (https): a hold with a pause keeps listening, joins both pieces, sends once.
  {
    const P = page({engine: 'browser'});
    await P.down();
    eq(P.calls.filter(c => c === 'sr.start').length, 1, 'browser started');
    eq(P.chips().includes('Listening'), false, 'not listening before audio');
    P.sr().onaudiostart();
    eq(P.chips()[P.chips().length - 1], 'Listening', 'listening on audio start');
    P.sr().onresult({results: [[{transcript: 'press tab'}]]});
    await P.advance(1500);
    await P.srEnd();  // a pause
    await P.advance(10);
    eq(P.calls.filter(c => c === 'sr.start').length, 2, 'listened again during the hold');
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'tab tab'}]]});
    await P.advance(1500);
    await P.up();
    ok(P.calls.includes('sr.stop'), 'stopped on let go');
    eq(P.sends().length, 0, 'waits for the final words');
    P.sr().onresult({results: [[{transcript: 'tab tab'}]]});
    await P.srEnd();
    eq(P.sends().length, 1, 'one send');
    eq(P.param(P.sends()[0], 'text'), 'press tab tab tab', 'words joined without loss');
    await P.advance(5000);
    eq(P.sends().length, 1, 'the bounded wait sends nothing more');
  }
  // 4. Browser: the final words never come: the bounded wait sends what was heard.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'open mail'}]]});
    await P.advance(2000);
    await P.up();
    await P.advance(1499);
    eq(P.sends().length, 0, 'not yet');
    await P.advance(2);
    eq(P.sends().length, 1, 'sent at the bounded wait');
    eq(P.param(P.sends()[0], 'text'), 'open mail', 'words');
    ok(P.calls.includes('sr.abort'), 'engine stopped');
    await P.srEnd();
    eq(P.sends().length, 1, 'a late end sends nothing');
  }
  // 5. A second finger, the right mouse button, and the wrong pointer's up are ignored.
  {
    const P = page({engine: 'browser'});
    await P.down(1);
    await P.down(2, {isPrimary: false});
    await P.down(3, {button: 2});
    eq(P.calls.filter(c => c === 'sr.start').length, 1, 'one start');
    await P.advance(2000);
    await P.up(7);
    eq(P.g('talkPhase') === 'warming' || P.g('talkPhase') === 'held', true, 'still held after another finger lifted');
    // A child of the stage losing its implicit capture is not a lost touch.
    P.fire('stage:lostpointercapture', P.event('lostpointercapture', P.els.face, 1));
    eq(P.g('talkPointer'), 1, 'still the same finger');
  }
  // 6. Android takes the touch mid-hold (pointercancel): listening goes on, the words wait on Send it, one tap sends.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'turn up the volume'}]]});
    await P.advance(1500);
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));
    eq(P.g('talkPhase'), 'quiet', 'listens on');
    await P.srEnd();
    eq(P.sends().length, 0, 'nothing sent without Send it');
    ok(P.els.sendHeardBtn.classList.contains('on'), 'Send it shown');
    P.g('sendHeardNow()');
    await P.settle();
    eq(P.sends().length, 1, 'one send after Send it');
    eq(P.param(P.sends()[0], 'text'), 'turn up the volume', 'offered words');
    P.g('sendHeardNow()');
    eq(P.sends().length, 1, 'twice is still once');
  }
  // 7. Send it times out: nothing is sent.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'delete everything'}]]});
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));
    await P.srEnd();
    await P.advance(15001);
    ok(!P.els.sendHeardBtn.classList.contains('on'), 'offer gone');
    eq(P.sends().length, 0, 'nothing sent');
  }
  // 8. App 1.1: ready only from the app, stale callbacks dropped, final words after let go, one send.
  {
    const P = page({engine: 'app', fingerStillDown: true,
                    answers: {'/words': '{"words":["Motorcycle Man","Morning","Spotify"],"ownMic":false}'}});
    await P.settle();  // the names came
    eq(P.fetches.filter(u => u.startsWith('/words')).length, 1, 'names asked for once');
    await P.down();
    const key = P.key();
    ok(P.calls.includes('app.listen en-US ' + key + ' {"hints":["Motorcycle Man","Morning","Spotify"]}'),
       'app listens with the key and the names: ' + P.calls.join(' | '));
    eq(P.chips().includes('Listening'), false, 'not before ready');
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`);
    eq(P.chips()[P.chips().length - 1], 'Listening', 'ready');
    P.g(`window.onAndroidSpeech('hello', false, 'abcd0123-99')`);
    eq(P.g('live'), '', 'stale key ignored');
    P.g(`window.onAndroidSpeech('hello there', false, '${key}')`);
    await P.advance(2000);
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));  // the web view let go, the finger didn't
    eq(P.calls.includes('app.fingerDown'), true, 'asked the app');
    eq(P.g('talkPhase'), 'held', 'the app says the finger is still down');
    P.g(`window.onAndroidFinger('up', '${key}')`);
    ok(P.calls.includes('app.doneListening ' + key), 'done listening');
    P.g(`window.onAndroidSpeech('hello there friend', true, '${key}')`);
    await P.settle();
    eq(P.sends().length, 1, 'one send');
    eq(P.param(P.sends()[0], 'text'), 'hello there friend', 'final words');
    eq(P.fetches.filter(u => u.startsWith('/hold_start') && /mic=phone/.test(u)).length, 1, 'phone mic hold_start');
  }
  // 9. App 1.1: the final words don't come within 4 s: the words so far go, the app is told to cancel.
  {
    const P = page({engine: 'app'});
    await P.down();
    const key = P.key();
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`);
    P.g(`window.onAndroidSpeech('open notepad', false, '${key}')`);
    await P.advance(2000);
    await P.up();
    await P.advance(4001);
    eq(P.sends().length, 1, 'sent at the bounded wait');
    eq(P.param(P.sends()[0], 'text'), 'open notepad', 'words so far');
    ok(P.calls.includes('app.cancelListening ' + key), 'cancelled');
    P.g(`window.onAndroidSpeech('open notepad now', true, '${key}')`);
    await P.settle();
    eq(P.sends().length, 1, 'a late final sends nothing');
  }
  // 10. App 1.0: pieces end at each pause and listen again; fast empties back off and stop after six.
  {
    const P = page({engine: 'app1'});
    await P.down();
    eq(P.chips()[P.chips().length - 1], 'Listening', 'app 1.0 shows listening at once');
    P.g(`window.onAndroidSpeech('open', false)`);
    await P.advance(1500);
    P.g(`window.onAndroidSpeech('open', true)`);  // the piece ended
    await P.advance(1);
    eq(P.calls.filter(c => c.startsWith('app.startListening')).length, 2, 'listened again');
    P.g(`window.onAndroidSpeech('mail', false)`);
    await P.advance(1500);
    await P.up();
    ok(P.calls.includes('app.stopListening'), 'stopped');
    P.g(`window.onAndroidSpeech('mail', true)`);
    await P.settle();
    eq(P.sends().length, 1, 'one send');
    eq(P.param(P.sends()[0], 'text'), 'open mail', 'both pieces');
    const Q = page({engine: 'app1'});
    await Q.down();
    for (let i = 0; i < 8; i++) { Q.g(`window.onAndroidSpeechError('')`); await Q.advance(1000); }
    eq(Q.calls.filter(c => c.startsWith('app.startListening')).length, 6, 'stops listening again after six fast empties');
    ok(/stopped/.test(Q.lastSaid()), 'says the ears stopped');
  }
  // 11. Leaving the app mid-hold drops the press: nothing reaches the PC; with the PC's microphone, a drop.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'send the email'}]]});
    await P.hide();
    eq(P.g('talkPhase'), 'off', 'dropped');
    await P.srEnd();
    await P.advance(20000);
    eq(P.sends().length, 0, 'nothing sent');
    const Q = page({engine: 'pc', answers: {'/hold_start': READY}});
    await Q.down();
    await Q.advance(1000);
    await Q.hide();
    eq(Q.sends().length, 1, 'one stop');
    eq(Q.param(Q.sends()[0], 'drop'), '1', 'a drop');
  }
  // 12. A reply during a press waits silently; after a press that sends nothing it shows.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.g(`showReply('The weather is sunny.')`);
    eq(P.g('heldReply'), 'The weather is sunny.', 'held');
    ok(!P.said().includes('The weather is sunny.'), 'not shown during the press');
    await P.advance(2000);
    await P.up();
    await P.srEnd();
    ok(/didn't hear anything/.test(P.lastSaid()), 'nothing heard');
    await P.advance(1800);
    eq(P.lastSaid(), 'The weather is sunny.', 'held reply shown');
    eq(P.sends().length, 0, 'nothing sent');
  }
  // 13. The widget's "listen" is a tap; a browser tap that hears words sends them when the talking stops.
  {
    const P = page({engine: 'browser'});
    P.g(`window.appAction('listen')`);
    await P.settle();
    eq(P.g('talkPhase'), 'quiet', 'forced tap');
    eq(P.g('tapListening'), 'phone', 'phone listens');
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'what time is it'}]]});
    await P.srEnd();
    eq(P.sends().length, 1, 'sent at the quiet');
    eq(P.g('tapListening'), false, 'tap over');
  }
  // 14. The microphone never says it's ready: a hold says so, a tap gives up.
  {
    const P = page({engine: 'browser'});
    await P.down();
    await P.advance(6001);
    ok(/didn't start/.test(P.lastSaid()), 'says the mic did not start');
    eq(P.chips().includes('Listening'), false, 'never said listening');
  }
  // 15. A PC that answers /hold_start in JSON: "Listening" waits for ready; Test my voice writes words down, runs nothing.
  {
    const P = page({engine: 'pc', answers: {'/hold_start': '{"ok":true,"ready":true}',
                                            '/hold_stop': '{"reply":"","heard":"open notepad","engine":"pack","ms":900}'}});
    P.g('openVoiceCheck()');
    await P.down(1, {on: 'voiceHold'});
    eq(P.els.voiceHold.captured, 1, 'voice button captured');
    eq(P.g('pcChecks'), true, 'learned the PC can check');
    await P.advance(2000);
    P.fire('window:pointerup', P.event('pointerup', P.els.voiceHold, 1));
    await P.settle();
    eq(P.sends().length, 1, 'one stop');
    eq(P.param(P.sends()[0], 'check'), '1', 'only written down');
    eq(P.els.voiceHeard.textContent, '“open notepad”', 'shows the words');
    ok(/sharp ears on your PC/.test(P.els.voiceHow.textContent), 'says who heard: ' + P.els.voiceHow.textContent);
    eq(P.g('commandSeq'), 0, 'no command sent');
    // A PC from before: Test my voice drops the recording and says to update.
    const Q = page({engine: 'pc', answers: {'/hold_start': 'ok'}});
    Q.g('openVoiceCheck()');
    await Q.down(1, {on: 'voiceHold'});
    await Q.advance(2000);
    Q.fire('window:pointerup', Q.event('pointerup', Q.els.voiceHold, 1));
    await Q.settle();
    eq(Q.sends().length, 1, 'one stop');
    eq(Q.param(Q.sends()[0], 'drop'), '1', 'dropped');
    ok(/Update Motorcycle Man/.test(Q.els.voiceHow.textContent), 'asks to update');
  }
  // 16. A new press while the last one waits for its final words: the last one's words go now, once; its late end
  // can't touch the new press; the new press's start, refused while the old one stops, is tried again.
  {
    const P = page({engine: 'browser'});
    await P.down(1);
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'first thing'}]]});
    await P.advance(1000);
    await P.up(1);
    eq(P.g('talkPhase'), 'ending', 'waiting for final words');
    await P.down(2);
    eq(P.sends().length, 1, 'the first press sent');
    eq(P.param(P.sends()[0], 'text'), 'first thing', 'its words');
    eq(P.g('talkPhase'), 'warming', 'the new press is on');
    await P.srEnd();  // the old recognizer's end
    eq(P.g('talkPhase'), 'warming', 'an old end changed nothing');
    await P.advance(151);
    eq(P.calls.filter(c => c === 'sr.start').length, 2, 'the new press started its listening');
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'second thing'}]]});
    await P.advance(1000);
    await P.up(2);
    await P.srEnd();
    eq(P.sends().length, 2, 'two presses, two sends');
    eq(P.param(P.sends()[1], 'text'), 'second thing', 'second words');
    ok(P.param(P.sends()[0], 's') !== P.param(P.sends()[1], 's'), 'two keys');
  }
  // 17. App 1.1 tap: the app listens until the quiet; another tap is "done talking".
  {
    const P = page({engine: 'app'});
    await P.down();
    const key = P.key();
    await P.advance(100);
    await P.up();
    ok(P.calls.includes('app.listenUntilQuiet ' + key), 'until quiet');
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`);
    ok(/just talk/.test(P.lastSaid()), 'says just talk: ' + P.lastSaid());
    P.g(`window.onAndroidSpeech('call mom', false, '${key}')`);
    await P.down(2);
    ok(P.calls.includes('app.doneListening ' + key), 'done listening');
    P.g(`window.onAndroidSpeech('call mom', true, '${key}')`);
    await P.settle();
    eq(P.sends().length, 1, 'one send');
    eq(P.param(P.sends()[0], 'auto'), null, 'phone words need no auto');
    eq(P.param(P.sends()[0], 'text'), 'call mom', 'words');
  }
  // 18. A new press clears a waiting Send it without sending it.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'buy it'}]]});
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));
    await P.srEnd();
    ok(P.els.sendHeardBtn.classList.contains('on'), 'offered');
    await P.down(2);
    ok(!P.els.sendHeardBtn.classList.contains('on'), 'offer gone');
    await P.advance(500);
    await P.up(2);
    await P.srEnd();
    eq(P.sends().length, 0, 'the offer was never sent');
  }
  // 19. A PC tap left while it listens: its recording is dropped and its answer is not shown.
  {
    const Q = page({engine: 'pc', answers: {'/hold_start': READY}});
    let answer;
    Q.context.fetch = (orig => url => {
      if (url.startsWith('/hold_stop') && !/drop=1/.test(url)) {
        Q.fetches.push(url);
        return new Promise(r => { answer = () => r({json: () => Promise.resolve({reply: 'Opened Spotify.'})}); });
      }
      return orig(url);
    })(Q.context.fetch);
    await Q.down();
    await Q.advance(100);
    await Q.up();
    eq(Q.g('tapListening'), 'pc', 'listening on the PC');
    await Q.hide();
    eq(Q.sends().length, 2, 'the auto stop and a drop');
    eq(Q.param(Q.sends()[1], 'drop'), '1', 'drop');
    answer();
    await Q.settle();
    ok(!Q.said().includes('Opened Spotify.'), 'the dropped answer is not shown');
  }
  // 20. Two minutes is the longest hold: what was heard goes by itself.
  {
    const P = page({engine: 'browser'});
    await P.down();
    P.sr().onaudiostart();
    P.sr().onresult({results: [[{transcript: 'a very long story'}]]});
    await P.advance(110000);
    ok(/10 seconds left/.test(P.lastSaid()), 'warned');
    await P.advance(10000);
    await P.srEnd();
    eq(P.sends().length, 1, 'sent at two minutes');
    await P.up();
    eq(P.sends().length, 1, 'letting go later sends nothing more');
  }
  // 21. The browser refuses to start four times: the press says so, and nothing is sent.
  {
    const P = page({engine: 'browser'});
    P.sr().start = function () { P.calls.push('sr.refused'); throw new Error('InvalidStateError'); };
    await P.down();
    await P.advance(1000);
    eq(P.calls.filter(c => c === 'sr.refused').length, 4, 'tried four times');
    ok(/didn't start/.test(P.lastSaid()), 'says so');
    await P.up();
    eq(P.sends().length, 0, 'nothing sent');
    eq(P.g('talkPhase'), 'off', 'press over');
    eq(P.lastSaid(), "The microphone didn't start. Try again.", 'after letting go');
  }
  // 22. The PC's microphone is on but silent (muted?): "Getting ready" stays, and the face asks; never "Listening".
  {
    const P = page({engine: 'pc', answers: {'/hold_start': '{"ok":true,"ready":false,"error":""}'}});
    await P.down();
    eq(P.chips().includes('Listening'), false, 'not listening while silent');
    eq(P.chips()[P.chips().length - 1], 'Getting ready', 'still getting ready');
    eq(P.lastSaid(), "The PC's microphone sounds silent. Is it muted?", 'asks about the mute');
    await P.advance(6100);  // past "Still waking up" and "didn't start": the question stays
    eq(P.lastSaid(), "The PC's microphone sounds silent. Is it muted?", 'still asks about the mute');
    ok(!P.said().some(s => /waking up|didn't start/.test(s)), 'never says the microphone did not start');
    eq(P.chips().includes('Listening'), false, 'still not listening');
    // A tap on a silent microphone: the same words, no "just talk".
    const Q = page({engine: 'pc', answers: {'/hold_start': '{"ok":true,"ready":false,"error":""}'}});
    let started;
    Q.context.fetch = (orig => url => {
      if (url.startsWith('/hold_start')) {
        Q.fetches.push(url);
        return new Promise(r => { started = () => r({text: () => Promise.resolve('{"ok":true,"ready":false,"error":""}')}); });
      }
      if (url.startsWith('/hold_stop')) { Q.fetches.push(url); return new Promise(() => {}); }
      return orig(url);
    })(Q.context.fetch);
    await Q.down();
    await Q.advance(100);
    await Q.up();
    eq(Q.g('tapListening'), 'pc', 'the tap listens on the PC');
    started();
    await Q.settle();
    eq(Q.lastSaid(), "The PC's microphone sounds silent. Is it muted?", 'a tap asks too');
    ok(!Q.said().includes("I'm listening… just talk."), 'never "just talk" while silent');
  }
  // 23. Another phone's press took the PC's microphone: no "Done!", the face says what happened and is ready again.
  {
    const P = page({engine: 'pc', answers: {'/hold_start': READY, '/hold_stop': '{"reply":"","replaced":true}'}});
    await P.down();
    await P.advance(2000);
    await P.up();
    await P.settle();
    eq(P.sends().length, 1, 'one stop');
    eq(P.lastSaid(), "The PC's microphone was taken over. Try again.", 'says it was taken over');
    ok(!P.said().includes('Done!'), 'no Done!');
    eq(P.g('awaitingReply'), false, 'not waiting');
    eq(P.g('mode'), 'idle', 'idle');
    // Test my voice, taken over: the same words in its own panel.
    const Q = page({engine: 'pc', answers: {'/hold_start': READY, '/hold_stop': '{"reply":"","replaced":true}'}});
    Q.g('openVoiceCheck()');
    await Q.down(1, {on: 'voiceHold'});
    await Q.advance(2000);
    Q.fire('window:pointerup', Q.event('pointerup', Q.els.voiceHold, 1));
    await Q.settle();
    eq(Q.els.voiceHow.textContent, "The PC's microphone was taken over. Try again.", 'the panel says so');
  }
  // 24. App 1.0 with this page: no names asked for, its own way of listening, one send.
  {
    const P = page({engine: 'app1'});
    await P.settle();
    eq(P.fetches.filter(u => u.startsWith('/words')).length, 0, 'app 1.0 needs no names');
    await P.down();
    ok(P.calls.includes('app.startListening en-US'), 'listens the 1.0 way');
    P.g(`window.onAndroidSpeech('open mail', false)`);
    await P.advance(1500);
    await P.up();
    P.g(`window.onAndroidSpeech('open mail', true)`);
    await P.settle();
    eq(P.sends().length, 1, 'one send');
    eq(P.param(P.sends()[0], 'text'), 'open mail', 'its words');
    // The hidden switch: the app is asked to record itself.
    const Q = page({engine: 'app', answers: {'/words': '{"words":["Motorcycle Man"],"ownMic":true}'}});
    await Q.settle();
    await Q.down();
    ok(Q.calls.includes('app.listen en-US ' + Q.key() + ' {"hints":["Motorcycle Man"],"ownMic":true}'), 'own mic asked');
  }
  // 25. Piece by piece, a restart gap is in the press's numbers (never its words).
  {
    const P = page({engine: 'app'});
    await P.down();
    const key = P.key();
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'loop')`);
    P.g(`window.onAndroidSpeech('open', false, '${key}')`);
    P.g(`window.onAndroidSpeechState('gap', '${key}', 'loop')`);
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'loop')`);
    await P.advance(2000);
    await P.up();
    P.g(`window.onAndroidSpeech('open mail', true, '${key}')`);
    await P.settle();
    const talk = JSON.parse(P.param(P.sends()[0], 'talk'));
    eq(talk.how, 'gap', 'the gap is logged');
    eq(talk.r, 1, 'one restart');
    ok(!/mail/.test(P.param(P.sends()[0], 'talk')), 'no words in the numbers');
  }
  console.log('flows ok');
})().catch(err => { console.error(err.stack || String(err)); process.exit(1); });
"""


# A page from before an update reloads itself, but only when nothing is going on, and once per build.
RELOAD_JS = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const eq = (a, b, what) => { if (a !== b) throw new Error((what || '') + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
function page(state) {
  const store = {};
  const context = Object.assign({
    PAGE_BUILD: 'aaaa', talkPhase: 'off', tapListening: false, busy: false, awaitingReply: false, sendHeard: null,
    reloads: 0, Array,
    document: {querySelectorAll: () => state.open ? [{style: {display: 'block'}}] : []},
    sessionStorage: {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }},
  }, state);
  context.location = {reload: () => { context.reloads++; }};
  vm.createContext(context);
  vm.runInContext(code, context);
  return context;
}
let P = page({});
P.pageChanged('aaaa');
eq(P.reloads, 0, 'same build');
P.pageChanged('');
eq(P.reloads, 0, 'a PC that says nothing');
for (const busy of [{talkPhase: 'held'}, {tapListening: 'pc'}, {busy: true}, {awaitingReply: true}, {sendHeard: {}}, {open: true}]) {
  P = page(busy);
  P.pageChanged('bbbb');
  eq(P.reloads, 0, 'not while ' + JSON.stringify(busy));
}
P = page({});
P.pageChanged('bbbb');
eq(P.reloads, 1, 'reloads when idle');
P.pageChanged('bbbb');
eq(P.reloads, 1, 'once for each build');
console.log('reload ok');
"""


def page_function(page: str, name: str) -> str:
    at = page.index(f"function {name}(")
    return page[at:page.index("\n}\n", at) + 3]


@unittest.skipUnless(NODE, "Node isn't installed")
class TalkInNodeTests(unittest.TestCase):
    def test_a_quick_press_is_a_tap_and_no_word_is_dropped(self):
        ran = run_node(RULES_JS, app.ANDROID_WEB_CLIENT)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("rules ok", ran.stdout)

    def test_every_press_sends_once_or_not_at_all(self):
        ran = run_node(FLOWS_JS, talk_code(app.ANDROID_WEB_CLIENT))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("flows ok", ran.stdout)

    def test_a_page_from_before_an_update_reloads_when_idle(self):
        ran = run_node(RELOAD_JS, page_function(app.ANDROID_WEB_CLIENT, "pageChanged"))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("reload ok", ran.stdout)


class PageBuildTests(unittest.TestCase):
    def test_the_page_knows_its_build_and_watches_the_pcs(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertRegex(app.PAGE_BUILD, r"^[0-9a-f]{10}$")
        self.assertIn(f'const PAGE_BUILD = "{app.PAGE_BUILD}";', page)
        self.assertNotIn("/*PAGE_BUILD*/", page)
        self.assertIn("pageChanged(state.page);", page)
        with mock.patch.object(app, "get_task_status", return_value={"status": "Status: Ready!", "running": False}):
            self.assertEqual(app.phone_status()["page"], app.PAGE_BUILD)

    def test_listening_is_said_only_when_the_pc_hears(self):
        # V1-8: "I'm listening" only on ready: true; on but silent keeps "Getting ready" and asks about the mute.
        page = app.ANDROID_WEB_CLIENT
        started = page[page.index("function pcStarted(key, answer) {"):page.index("function pcHeard(key, d) {")]
        self.assertIn("const ready = !trouble && !!reply && reply.ready === true;", started)
        self.assertIn("const silent = !trouble && !!reply && reply.ok === true && reply.ready === false;", started)
        self.assertIn("else if (ready) talkIsReady('');", started)
        self.assertIn("else if (silent) {\n    pcSilent = true;\n    talkSay(PC_SILENT);", started)
        self.assertIn('const PC_SILENT = "The PC\'s microphone sounds silent. Is it muted?";', page)
        self.assertIn('const PC_TAKEN = "The PC\'s microphone was taken over. Try again.";', page)


class PressGuardTests(unittest.TestCase):
    def test_the_press_follows_one_finger_and_ends_when_the_app_leaves(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ("setPointerCapture(event.pointerId)", "addEventListener('pointercancel', lostPointer)",
                      "addEventListener('lostpointercapture', lostPointer)", "event.pointerId !== talkPointer",
                      "if (!event.isPrimary || event.button > 0 || talkPointer !== null) return;",
                      "addEventListener('contextmenu'", "'visibilitychange'", "addEventListener('pagehide'",
                      "typeof androidSpeech.listen === 'function'", "holdApp.fingerDown()",
                      "padding: 78px 22px 48px; -webkit-touch-callout: none; }"):
            self.assertIn(piece, page)
        action = page[page.index("window.appAction = what =>"):page.index("// Several PCs:")]
        self.assertNotIn("new PointerEvent('pointerdown'", action)  # the widget's tap is a press of its own
        self.assertIn("startTalk(null, true, false);", action)

    def test_a_reply_waits_while_you_talk(self):
        page = app.ANDROID_WEB_CLIENT
        show = page[page.index("function showReply(reply) {"):page.index("function showHeldReply()")]
        self.assertIn("if (talkPhase !== 'off' || sendHeard) {", show)
        self.assertIn("if (!words || talkPhase !== 'off') return;", page)  # and never speaks over you
        self.assertIn('<button id="sendHeardBtn" onclick="sendHeardNow()"', page)  # written in the page, not made
        self.assertIn('<div class="backdrop" id="voiceModal"', page)
        self.assertEqual(page.count("innerHTML"), 1)


class BridgeTests(unittest.TestCase):
    def test_the_page_calls_only_what_the_app_offers(self):
        page = app.ANDROID_WEB_CLIENT
        with open(os.path.join(ANDROID, "src", "app", "motorcycleman", "remote", "MainActivity.java"), encoding="utf-8") as handle:
            java = handle.read()
        offered = set(re.findall(r"@JavascriptInterface\s+public\s+(?:void|boolean|String|int)\s+(\w+)\(", java))
        for name in set(re.findall(r"\bandroidSpeech\.(\w+)\(", page)):
            self.assertIn(name, offered)
        called = set(re.findall(r"\bholdApp\.(\w+)\(", page))
        self.assertTrue({"listen", "listenUntilQuiet", "doneListening", "cancelListening", "fingerDown"} <= called)
        self.assertLessEqual(called, offered)


if __name__ == "__main__":
    unittest.main()
