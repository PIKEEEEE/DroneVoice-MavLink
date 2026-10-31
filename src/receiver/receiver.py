#!/usr/bin/env python3
"""MAVLink2 TUNNEL -> libopus 16 kHz mono -> PulseAudio 48 kHz stereo.

Raspberry Pi OS setup (run playback as the logged-in desktop user, without sudo):
  sudo apt install python3-venv libopus0 pulseaudio-utils
  python3 -m venv .venv
  .venv/bin/pip install pymavlink pyserial numpy scipy
  .venv/bin/python receiver.py --list-devices
  .venv/bin/python receiver.py --device bluez_output.YOUR_SINK_NAME

PipeWire requires its pipewire-pulse service. Pair/connect the Bluetooth device
and select its A2DP playback profile first. UART must be enabled with the serial
console disabled; the user needs serial-port permissions (usually dialout).

WIRE CONTRACT (verified against DroneVoicePlugin.cs v0.4.0):
One TUNNEL per packet, payload_type=32768. Header is <BHIB (8 bytes):
  byte 0: version=1; bytes 1..2: uint16 sequence (little endian);
  bytes 3..6: uint32 timestamp in milliseconds; byte 7: flags.
Flag bit 0 is end-of-talk: an 8-byte packet with no Opus data is NORMAL.
Audio packets have flags=0 and 1..120 Opus bytes (20 ms, 16 kHz mono).
Timestamps are metadata; playout uses monotonic time and sequence numbers.
MAVLink2 trailing zeros are restored before applying payload_length.
--header-mode raw supports older/custom 8-byte headers using --seq-* options.

--self-test runs hardware-independent checks. Ctrl+C exits cleanly. Serial or
audio-server failures exit nonzero; a supervisor may restart the process.
"""
import argparse
import ctypes as C
import ctypes.util
import logging
import json
import os
import queue
import select
import shutil
import struct
import subprocess
import sys
import threading
import time

LOG = logging.getLogger("receiver")
FRAME = 0.020


class JitterBuffer:
    """Bounded reorder window, modular sequence numbers, PLC on missing frames."""
    def __init__(self, bits=32, delay=0.080, capacity=25):
        self.mod = 1 << bits
        self.delay, self.capacity = delay, capacity
        self.lock = threading.Lock()
        self.frames = {}
        self.expected = None
        self.ready_at = 0.0
        self.last_rx = 0.0
        self.generation = 0
        self.ended = False

    def push(self, seq, packet, now):
        with self.lock:
            seq %= self.mod
            # Resume/restart after a stream gap. A large forward discontinuity
            # also rebuffers instead of playing seconds of fabricated PLC.
            distance = 0 if self.expected is None else (seq-self.expected) % self.mod
            if (self.expected is None or now-self.last_rx > 0.75
                    or self.capacity <= distance < self.mod//2):
                self.frames.clear()
                self.expected, self.ready_at = seq, now+self.delay
                self.generation += 1
                self.ended = False
            elif distance >= self.mod//2:
                # Reordering during initial fill is allowed within the window.
                backwards = (self.expected-seq) % self.mod
                if now < self.ready_at and backwards < self.capacity:
                    self.expected = seq
                else:
                    return False
            if seq in self.frames or len(self.frames) >= self.capacity:
                return False
            self.frames[seq] = packet
            self.last_rx = now
            return True

    def pop(self, now):
        with self.lock:
            if self.expected is None or now < self.ready_at:
                return False, None, self.generation
            if now-self.last_rx > 0.75:
                self.frames.clear()
                self.expected = None
                return False, None, self.generation
            if self.ended:
                if not self.frames:
                    return False, None, self.generation
                self.expected = min(self.frames, key=lambda seq: (seq-self.expected) % self.mod)
                self.ready_at = now+self.delay
                self.ended = False
                return False, None, self.generation
            packet = self.frames.pop(self.expected, None)
            self.expected = (self.expected+1) % self.mod
            if packet == b"":
                # Ordered PTT end marker: no decoder call, no fabricated PLC tail.
                # Retain any already-arrived packets from a subsequent PTT press.
                self.ended = True
                return False, None, self.generation
            return True, packet, self.generation


def parse_audio(msg, args):
    n = int(msg.payload_length)
    if not 8 <= n <= 128:
        raise ValueError(f"TUNNEL payload_length={n}, expected 8..128")
    raw = bytes(msg.payload).ljust(128, b"\0")[:n]
    if args.header_mode == "plugin":
        version, seq, stamp_ms, flags = struct.unpack("<BHIB", raw[:8])
        if version != 1:
            raise ValueError(f"Unsupported DroneVoice header version={version}")
        if flags & ~1:
            raise ValueError(f"Unsupported DroneVoice flags={flags}")
        if flags & 1:
            if n != 8:
                raise ValueError("PTT end marker must have payload_length=8")
            return seq, b""  # Control marker, never pass to Opus.
        if n == 8:
            raise ValueError("Audio packet has a header but no Opus data (flags=0)")
    else:
        if n == 8:
            raise ValueError("Raw audio packet has no Opus data")
        seq = int.from_bytes(raw[args.seq_offset:args.seq_offset+args.seq_bytes],
                             args.byte_order)
    return seq, raw[8:]


class Opus:
    def __init__(self, np):
        self.np = np
        self.lib = C.CDLL(ctypes.util.find_library("opus") or "libopus.so.0")
        self.lib.opus_decoder_create.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_int)]
        self.lib.opus_decoder_create.restype = C.c_void_p
        self.lib.opus_decoder_destroy.argtypes = [C.c_void_p]
        self.lib.opus_decoder_destroy.restype = None
        self.lib.opus_decode_float.argtypes = [C.c_void_p, C.c_void_p, C.c_int,
                                               C.POINTER(C.c_float), C.c_int, C.c_int]
        self.lib.opus_decode_float.restype = C.c_int
        self.lib.opus_packet_get_nb_samples.argtypes = [C.c_void_p, C.c_int, C.c_int]
        self.lib.opus_packet_get_nb_samples.restype = C.c_int
        self.state = None
        self.reset()

    def reset(self):
        self.close()
        error = C.c_int()
        self.state = self.lib.opus_decoder_create(16000, 1, C.byref(error))
        if error.value or not self.state:
            raise RuntimeError("opus_decoder_create failed: %s" % error.value)

    def decode(self, packet):
        data = C.create_string_buffer(packet) if packet is not None else None
        length = len(packet) if packet is not None else 0
        if data is not None:
            samples = self.lib.opus_packet_get_nb_samples(data, length, 16000)
            if samples != 320:
                raise ValueError("Opus packet is not 20 ms at 16 kHz: %s" % samples)
        pcm = self.np.empty(320, dtype=self.np.float32)
        n = self.lib.opus_decode_float(self.state, data, length,
                    pcm.ctypes.data_as(C.POINTER(C.c_float)), 320, 0)
        if n != 320:
            raise ValueError("Opus decode failed / unexpected samples: %s" % n)
        return pcm

    def close(self):
        if self.state:
            self.lib.opus_decoder_destroy(self.state)
            self.state = None


class Resampler:
    """Stateful 3x polyphase FIR; retains history across 20 ms boundaries."""
    def __init__(self, np, signal):
        self.np, self.signal = np, signal
        self.taps = signal.firwin(63, 1/3, window=("kaiser", 5.0))*3
        self.reset()

    def reset(self):
        self.state = self.np.zeros(len(self.taps)-1)

    def convert(self, pcm):
        up = self.np.zeros(960)
        up[::3] = pcm
        mono, self.state = self.signal.lfilter(self.taps, [1.0], up, zi=self.state)
        stereo = self.np.repeat(self.np.clip(mono, -1, 1)[:, None], 2, axis=1)
        return stereo.astype("<f4").tobytes()


def serial_worker(link, mavlink, args, jitter, stop, errors, stats):
    next_hb = 0.0
    selected = None
    bad = 0
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_hb:
                link.mav.heartbeat_send(mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                    mavlink.MAV_AUTOPILOT_INVALID, 0, 0, mavlink.MAV_STATE_ACTIVE)
                stats["heartbeats"] += 1
                next_hb = now+1.0
            msg = link.recv_match(blocking=True, timeout=0.05)
            if msg is None:
                continue
            stats['messages'] += 1
            if msg.get_type() == 'BAD_DATA':
                stats['bad_data'] += 1
            if msg.get_type() != 'TUNNEL':
                continue
            stats['tunnel_any'] += 1
            stats['last_tunnel'] = ('src=%d/%d dst=%d/%d type=%d len=%d' % (
                msg.get_srcSystem(), msg.get_srcComponent(), msg.target_system,
                msg.target_component, int(msg.payload_type), int(msg.payload_length)))
            if int(msg.payload_type) != args.payload_type:
                stats["wrong_type"] += 1
                continue
            if msg.target_system not in (0, args.sysid) or msg.target_component not in (0, args.compid):
                stats["wrong_target"] += 1
                continue
            source = (msg.get_srcSystem(), msg.get_srcComponent())
            if args.sender_sys is not None and source[0] != args.sender_sys:
                stats["wrong_source"] += 1
                continue
            if args.sender_comp is not None and source[1] != args.sender_comp:
                stats["wrong_source"] += 1
                continue
            # Do not interleave independent Opus decoder streams.
            if selected is not None and source != selected:
                stats["wrong_source"] += 1
                continue
            stats["tunnel"] += 1
            try:
                seq, packet = parse_audio(msg, args)
            except (ValueError, TypeError, AttributeError) as exc:
                bad += 1
                stats["invalid"] += 1
                if bad == 1 or bad % 100 == 0:
                    LOG.warning("Invalid audio packet (%d): %s", bad, exc)
                continue
            if selected is None:
                selected = source
                LOG.info("Audio sender SYS=%d COMP=%d", *source)
            if packet == b"":
                stats["end"] += 1
                LOG.info("PTT END received: seq=%d", seq)
            else:
                stats["audio"] += 1
                if stats["audio"] == 1:
                    LOG.info("First AUDIO packet: seq=%d opus_bytes=%d", seq, len(packet))
            if not jitter.push(seq, packet, time.monotonic()):
                stats["rejected"] += 1
    except Exception as exc:
        errors.put(exc)
        stop.set()


def write_audio(proc, data, stop):
    """Nonblocking writes: disconnected/stalled audio cannot hang shutdown."""
    view = memoryview(data)
    deadline = time.monotonic()+2.0
    while view and not stop.is_set():
        if proc.poll() is not None:
            raise RuntimeError("pacat exited; inspect its audio-server error above")
        if time.monotonic() > deadline:
            raise RuntimeError("Audio output stalled for 2 seconds")
        _, writable, _ = select.select([], [proc.stdin], [], 0.05)
        if writable:
            try:
                n = os.write(proc.stdin.fileno(), view)
                if n == 0:
                    raise RuntimeError("Audio pipe closed")
                view = view[n:]
            except BlockingIOError:
                pass



def physical_outputs():
    try:
        raw = subprocess.check_output(['pactl', '--format=json', 'list', 'sinks'],
                                      text=True, stderr=subprocess.PIPE, timeout=5)
        items = json.loads(raw)
        return sorted({item['name'] for item in items
                       if item['name'].startswith(('alsa_output.', 'bluez_output.', 'bluez_sink.'))})
    except (ValueError, subprocess.CalledProcessError):
        raw = subprocess.check_output(['pactl', 'list', 'short', 'sinks'],
                                      text=True, stderr=subprocess.PIPE, timeout=5)
        return sorted({f[1] for line in raw.splitlines() if len(f := line.split()) >= 2
                       and f[1].startswith(('alsa_output.', 'bluez_output.', 'bluez_sink.'))})


def set_speaker_max_volume(name):
    # 100% is the sound server's normal full volume, without over-amplification.
    for args in [('set-sink-volume', name, '100%'), ('set-sink-mute', name, '0')]:
        subprocess.run(['pactl', *args], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=5)
    LOG.info('Speaker volume set to 100%% and unmuted: %s', name)


class SpeakerWriter:
    """Independent bounded queue/process: one disconnected speaker cannot block others."""
    def __init__(self, name):
        self.name, self.stop = name, threading.Event()
        self.frames = queue.Queue(maxsize=5)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def offer(self, data):
        try:
            self.frames.put_nowait(data)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(data)
            except queue.Full:
                pass

    def run(self):
        proc = None
        try:
            cmd = ['pacat', '--playback', '--raw', '--format=float32le', '--rate=48000',
                   '--channels=2', '--channel-map=front-left,front-right', '--latency-msec=80',
                   '--client-name=DroneVoiceDirect', '--stream-name=DroneVoiceDirect:'+self.name,
                   '--volume=65536',
                   '--device='+self.name]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0)
            os.set_blocking(proc.stdin.fileno(), False)
            LOG.info('Direct speaker connected: %s', self.name)
            while not self.stop.is_set():
                if proc.poll() is not None:
                    raise RuntimeError('pacat exited')
                try:
                    data = self.frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                write_audio(proc, data, self.stop)
        except Exception as exc:
            LOG.warning('Direct speaker stopped (%s): %s; will retry', self.name, exc)
        finally:
            if proc is not None:
                if proc.stdin:
                    proc.stdin.close()
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=0.5)


class DirectOutputs:
    def __init__(self, max_volume=False):
        self.writers, self.lock = {}, threading.Lock()
        self.max_volume = max_volume
        self.volume_ready = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()

    def refresh(self, names):
        removed = []
        with self.lock:
            for name in list(self.writers):
                if name not in names or not self.writers[name].thread.is_alive():
                    removed.append(self.writers.pop(name))
            for name in names:
                if self.stop.is_set():
                    break
                if name not in self.writers:
                    self.writers[name] = SpeakerWriter(name)
        for writer in removed:
            writer.close()

    def prepare_volumes(self, names):
        if not self.max_volume:
            return
        self.volume_ready.intersection_update(names)
        for name in names:
            if name not in self.volume_ready:
                try:
                    set_speaker_max_volume(name)
                    self.volume_ready.add(name)
                except (OSError, subprocess.SubprocessError) as exc:
                    LOG.warning('Volume setup pending (%s): %s; retrying', name, exc)

    def monitor(self):
        previous = None
        while not self.stop.is_set():
            try:
                names = physical_outputs()
                self.refresh(names)
                self.prepare_volumes(names)
                if names != previous:
                    LOG.info('Direct outputs (%d): %s', len(names), ', '.join(names) or 'waiting for devices')
                    previous = names
            except Exception as exc:
                LOG.warning('Audio device discovery: %s', exc)
            self.stop.wait(2)

    def write(self, data):
        with self.lock:
            writers = list(self.writers.values())
        for writer in writers:
            writer.offer(data)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)
        with self.lock:
            writers = list(self.writers.values())
            self.writers.clear()
        for writer in writers:
            writer.close()

def run(args):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Playback requires Linux / Raspberry Pi OS")
    if args.list_devices:
        return subprocess.run(["pactl", "list", "short", "sinks"], check=True).returncode
    import numpy as np
    if args.test_audio and args.all_devices:
        outputs = DirectOutputs(max_volume=args.max_volume)
        try:
            # Wait briefly for asynchronous device enumeration.
            time.sleep(2.5)
            t = np.arange(960)/48000
            deadline = time.monotonic()
            for frame in range(100):
                pcm = 0.08*np.sin(2*np.pi*440*(t+frame*FRAME))
                outputs.write(np.repeat(pcm[:, None], 2, axis=1).astype('<f4').tobytes())
                deadline += FRAME
                time.sleep(max(0, deadline-time.monotonic()))
            time.sleep(0.5)
        finally:
            outputs.close()
        LOG.info('Direct-output test completed. Check the connected speakers.')
        return 0
    if args.test_audio:
        command = ["pacat", "--playback", "--raw", "--format=float32le",
                   "--rate=48000", "--channels=2", "--channel-map=front-left,front-right"]
        if args.device:
            command.append("--device="+args.device)
        t = np.arange(48000*2)/48000
        tone = 0.08*np.sin(2*np.pi*440*t)
        tone *= np.minimum(1, t/0.02)*np.minimum(1, (2-t)/0.02)
        data = np.repeat(tone[:, None], 2, axis=1).astype("<f4").tobytes()
        LOG.info("Playing a 2-second test tone to %s (UART is not used)", args.device or "default")
        proc = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            proc.communicate(data, timeout=10)
            if proc.returncode:
                raise RuntimeError("Audio test failed: pacat exit %d" % proc.returncode)
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        LOG.info("Test tone sent. Check whether the Bluetooth speaker was audible.")
        return 0
    from scipy import signal
    # Import v2 dialect explicitly, independent of pre-existing MAVLINK20 env.
    os.environ["MAVLINK20"] = "1"
    from pymavlink import mavutil
    from pymavlink.dialects.v20 import common as mavlink
    mavutil.set_dialect("common")
    if not shutil.which("pacat"):
        raise RuntimeError("pacat missing: install pulseaudio-utils")
    stop, errors = threading.Event(), queue.Queue()
    stats = dict(tunnel=0, audio=0, end=0, invalid=0, rejected=0,
                 messages=0, bad_data=0, tunnel_any=0, wrong_type=0,
                 wrong_target=0, wrong_source=0, heartbeats=0, last_tunnel='none')
    bits = 16 if args.header_mode == "plugin" else args.seq_bytes*8
    jitter = JitterBuffer(bits, args.jitter_ms/1000, args.max_frames)
    link = proc = decoder = worker = outputs = None
    try:
        decoder = Opus(np)
        resampler = Resampler(np, signal)
        link = mavutil.mavlink_connection(args.port, baud=args.baud,
            source_system=args.sysid, source_component=args.compid, autoreconnect=False)
        if args.all_devices:
            outputs = DirectOutputs(max_volume=args.max_volume)
        else:
            command = ["pacat", "--playback", "--raw", "--format=float32le", "--rate=48000",
                       "--channels=2", "--channel-map=front-left,front-right", "--latency-msec=80",
                       "--client-name=DroneVoiceReceiver", "--stream-name=DroneVoiceReceiver",
                       "--property=application.id=org.dronevoice.receiver", "--volume=65536"]
            if args.device:
                command.append("--device="+args.device)
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, bufsize=0)
            os.set_blocking(proc.stdin.fileno(), False)
        worker = threading.Thread(target=serial_worker,
            args=(link, mavlink, args, jitter, stop, errors, stats), daemon=True)
        worker.start()
        LOG.info("UART %s %d; heartbeat SYS=%d COMP=%d; sink=%s",
                 args.port, args.baud, args.sysid, args.compid, "ALL DIRECT" if args.all_devices else (args.device or "default"))
        LOG.info("Header mode=%s; jitter=%d ms", args.header_mode, args.jitter_ms)
        if args.header_mode == "plugin":
            LOG.info("DroneVoice v1: uint16 LE sequence at offset 1; PTT end markers supported")
        deadline, generation, bad = time.monotonic(), -1, 0
        silence = bytes(960*2*4)
        next_status = time.monotonic()+2
        decoded = concealed = decode_errors = 0
        peak = 0.0
        while not stop.is_set():
            stop.wait(max(0, deadline-time.monotonic()))
            if stop.is_set():
                break
            active, packet, current = jitter.pop(time.monotonic())
            if current != generation:
                decoder.reset()
                resampler.reset()
                generation = current
            if active:
                try:
                    pcm = decoder.decode(packet)
                    if packet is None:
                        concealed += 1
                    else:
                        decoded += 1
                        peak = max(peak, float(np.max(np.abs(pcm))))
                except ValueError as exc:
                    bad += 1
                    decode_errors += 1
                    if bad == 1 or bad % 100 == 0:
                        LOG.warning("Invalid Opus packet (%d): %s", bad, exc)
                    pcm = decoder.decode(None)
                data = resampler.convert(pcm)
            else:
                data = silence
            if outputs is not None:
                outputs.write(data)
            else:
                write_audio(proc, data, stop)
            now = time.monotonic()
            if now >= next_status:
                LOG.info("AUDIO received=%d decoded=%d end=%d peak=%.1f%% errors=%d",
                         stats['audio'], decoded, stats['end'], peak*100, decode_errors)
                LOG.info("LINK bytes=%s messages=%d tunnels=%d bad_data=%d hb_sent=%d",
                         getattr(link.mav, 'total_bytes_received', -1), stats['messages'],
                         stats['tunnel_any'], stats['bad_data'], stats['heartbeats'])
                LOG.info("FILTER type=%d target=%d source=%d invalid=%d jitter_drop=%d",
                         stats['wrong_type'], stats['wrong_target'], stats['wrong_source'],
                         stats['invalid'], stats['rejected'])
                LOG.info("LAST_TUNNEL %s", stats['last_tunnel'])
                peak = 0.0
                next_status = now+2
            deadline += FRAME
            # Do not burst overdue audio following a server/scheduler stall.
            if deadline < time.monotonic()-FRAME:
                deadline = time.monotonic()+FRAME
        if not errors.empty():
            raise RuntimeError("UART/heartbeat failure: %s" % errors.get())
    finally:
        stop.set()
        if outputs is not None:
            outputs.close()
        if proc is not None:
            if proc.stdin:
                proc.stdin.close()
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if worker is not None:
            worker.join(timeout=1)
        if link is not None:
            link.close()
        if decoder is not None:
            decoder.close()
    return 0


def self_test():
    import unittest
    class Tests(unittest.TestCase):
        def test_reorder_loss_wrap(self):
            j = JitterBuffer(16)
            j.push(65535, b"a", 1)
            j.push(1, b"c", 1.01)
            self.assertFalse(j.pop(1.02)[0])
            self.assertEqual(j.pop(1.081)[1], b"a")
            self.assertIsNone(j.pop(1.101)[1])
            self.assertEqual(j.pop(1.121)[1], b"c")
            self.assertFalse(j.push(65535, b"late", 1.13))

        def test_restart_bound_duplicate(self):
            j = JitterBuffer(capacity=10)
            j.push(50, b"a", 1)
            self.assertFalse(j.push(50, b"a", 1.01))
            j.push(500, b"b", 1.02)
            self.assertEqual(len(j.frames), 1)
            self.assertEqual(j.pop(1.11)[1], b"b")
            self.assertFalse(j.pop(2)[0])
            j.push(0, b"reset", 2.1)
            self.assertEqual(j.pop(2.19)[1], b"reset")

        def test_header_and_trailing_zeros(self):
            class M:
                payload_length = 11
                payload = struct.pack("<II", 123, 0)+b"x"
            args = argparse.Namespace(header_mode="raw", seq_offset=0, seq_bytes=4, byte_order="little")
            self.assertEqual(parse_audio(M(), args), (123, b"x\0\0"))
            M.payload_length = 129
            with self.assertRaises(ValueError):
                parse_audio(M(), args)
        def test_plugin_wire_audio_and_end(self):
            class M:
                pass
            m = M()
            args = argparse.Namespace(header_mode="plugin")
            m.payload = struct.pack("<BHIB", 1, 65535, 123456, 0)+b"opus"
            m.payload_length = 12
            self.assertEqual(parse_audio(m, args), (65535, b"opus"))
            m.payload = struct.pack("<BHIB", 1, 0, 123476, 1)
            m.payload_length = 8
            self.assertEqual(parse_audio(m, args), (0, b""))
            m.payload = struct.pack("<BHIB", 1, 1, 123496, 0)
            with self.assertRaises(ValueError):
                parse_audio(m, args)
            m.payload = struct.pack("<BHIB", 2, 1, 123496, 1)
            with self.assertRaises(ValueError):
                parse_audio(m, args)

        def test_ptt_end_and_fast_restart(self):
            j = JitterBuffer(16)
            j.push(65535, b"audio", 1)
            j.push(0, b"", 1.01)
            # Next talk already queued before the previous end is played.
            j.push(3, b"next", 1.02)
            self.assertEqual(j.pop(1.081)[1], b"audio")
            self.assertFalse(j.pop(1.101)[0])
            self.assertFalse(j.pop(1.121)[0])
            self.assertEqual(j.pop(1.202)[1], b"next")
            j.push(4, b"", 1.21)
            self.assertFalse(j.pop(1.222)[0])
            self.assertFalse(j.pop(1.242)[0])

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/serial0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--device", help="PulseAudio/PipeWire-Pulse sink name from --list-devices")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--max-volume", action="store_true",
                   help="set newly detected speaker volume to 100%% and unmute once per connection")
    p.add_argument("--all-devices", action="store_true",
                   help="send directly to every active physical speaker; detect hotplug")
    p.add_argument("--test-audio", action="store_true",
                   help="play a quiet 2-second 440 Hz test tone, without opening UART")
    p.add_argument("--sysid", type=int, default=1)
    p.add_argument("--compid", type=int, default=191)
    p.add_argument("--payload-type", type=int, default=32768)
    p.add_argument("--sender-sys", type=int)
    p.add_argument("--sender-comp", type=int)
    p.add_argument("--header-mode", choices=("plugin", "raw"), default="plugin",
                   help="plugin: DroneVoicePlugin v0.4 header (default); raw: custom header")
    p.add_argument("--seq-offset", type=int, default=0)
    p.add_argument("--seq-bytes", type=int, choices=(2, 4), default=4)
    p.add_argument("--byte-order", choices=("little", "big"), default="little")
    p.add_argument("--jitter-ms", type=int, default=80)
    p.add_argument("--max-frames", type=int, default=25)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.all_devices and args.device:
        p.error("choose --all-devices or --device")
    if not 0 <= args.seq_offset <= 8-args.seq_bytes:
        p.error("sequence field must fit inside the 8-byte header")
    if not (0 <= args.jitter_ms <= 500 and args.jitter_ms//20+2 <= args.max_frames <= 200):
        p.error("jitter-ms must be 0..500; max-frames must be >= jitter-ms/20+2 and <=200")
    for name in ("sysid", "compid", "sender_sys", "sender_comp"):
        v = getattr(args, name)
        if v is not None and not 1 <= v <= 255:
            p.error(name+" must be 1..255")
    if args.baud <= 0 or not 0 <= args.payload_type <= 65535:
        p.error("invalid baud or payload-type")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return self_test() if args.self_test else run(args)
    except KeyboardInterrupt:
        LOG.info("Stopped")
        return 0
    except Exception as exc:
        LOG.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
